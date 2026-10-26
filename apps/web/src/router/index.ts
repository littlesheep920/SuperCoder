import { createRouter, createWebHistory } from "vue-router";
import App from "../App.vue";
import ResourceListView from "../views/ResourceListView.vue";
import ResourceDetailView from "../views/ResourceDetailView.vue";
import RegisterView from "../views/RegisterView.vue";
import VersionsView from "../views/VersionsView.vue";
import ReviewsView from "../views/ReviewsView.vue";
import DependenciesView from "../views/DependenciesView.vue";
import DiscoveryView from "../views/DiscoveryView.vue";
import CapabilityFinderView from "../views/CapabilityFinderView.vue";
import RegistrationDraftView from "../views/RegistrationDraftView.vue";
import JobsView from "../views/JobsView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: ResourceListView },
    // Capability discovery: three-column console (filters | results | explanation).
    { path: "/discovery", component: DiscoveryView },
    // Ask for a capability in natural language; answers carry their evidence, and
    // a refusal is shown with the candidates that were considered.
    { path: "/find", component: CapabilityFinderView },
    { path: "/resources", component: ResourceListView },
    { path: "/resources/:id", component: ResourceDetailView, props: true },
    { path: "/register", component: RegisterView },
    // Draft a manifest from a description. Writes nothing; a human submits after.
    { path: "/register/draft", component: RegistrationDraftView },
    { path: "/resources/:id/versions", component: VersionsView, props: true },
    { path: "/resources/:id/reviews", component: ReviewsView, props: true },
    { path: "/resources/:id/dependencies", component: DependenciesView, props: true },
    { path: "/jobs", component: JobsView },
  ],
});

void App;
