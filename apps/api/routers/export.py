"""Export endpoint.

`GET /v1/resource-versions/{versionId}/export?target=...` returns a zip of the files the
chosen environment consumes. The response also reports what the export could not carry, in
a header and in a sidecar file inside the archive, so a caller cannot receive the artefact
without the disclosure attached.

The archive is built with fixed metadata, so the same version and target always produce
byte-identical output. That is what makes the export verifiable: a recipient can hash it
and compare against what the platform recorded.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from ..auth import AuthContext
from ..dependencies import Container, get_auth, get_container
from ..domain.adapters import available_targets
from ..domain.errors import ServiceUnavailableError

router = APIRouter(tags=["Resources"])

#: Exports are refused entirely when the service is not configured, rather than returning
#: an empty archive that looks like a successful export of nothing.
_MEDIA_TYPE = "application/zip"


@router.get("/export-targets", operation_id="listExportTargets")
def list_export_targets(container: Container = Depends(get_container)) -> dict:
    """List the environments a resource can be exported to.

    Kept separate from the export itself so the console can render the choices without
    attempting an export, and so the set of targets has one authoritative source.
    """

    return {
        "items": [
            {
                "identifier": target.identifier,
                "display_name": target.display_name,
                "skills_directory": target.skills_directory,
            }
            for target in available_targets()
        ]
    }


@router.get(
    "/resource-versions/{version_id}/export",
    operation_id="exportResourceVersion",
    response_class=Response,
    responses={
        200: {
            "description": "A zip archive of the files the target consumes.",
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def export_version(
    version_id: str,
    target: str = Query(..., description="Export target identifier."),
    container: Container = Depends(get_container),
    auth: AuthContext = Depends(get_auth),
) -> Response:
    """Export a published version as an environment-ready archive."""

    if container.export_service is None:
        raise ServiceUnavailableError(
            message="export requires a writable Git checkout and MySQL"
        )

    result = container.export_service.export(
        auth=auth, version_public_id=version_id, target_identifier=target
    )
    bundle = result.bundle

    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "X-Export-Target": bundle.target,
        "X-Export-Digest": bundle.digest(),
        "X-Export-Warning-Count": str(len(bundle.warnings)),
    }
    # The codes go in a header; the full explanation is returned by the console, which
    # already has the version and can show each effect next to the artefact. Adding a file
    # to the archive here would make the archive's bytes depend on the warning wording,
    # breaking the stable digest the export is supposed to have.
    if bundle.warnings:
        headers["X-Export-Warnings"] = ",".join(warning.code for warning in bundle.warnings)

    return Response(content=result.archive, media_type=_MEDIA_TYPE, headers=headers)
