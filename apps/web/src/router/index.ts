import { createRouter, createWebHistory } from "vue-router";
import App from "../App.vue";
import ResourceListView from "../views/ResourceListView.vue";
import ResourceDetailView from "../views/ResourceDetailView.vue";
import RegisterView from "../views/RegisterView.vue";
import VersionsView from "../views/VersionsView.vue";
import ReviewsView from "../views/ReviewsView.vue";
import DependenciesView from "../views/DependenciesView.vue";
import JobsView from "../views/JobsView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: ResourceListView },
    { path: "/resources", component: ResourceListView },
    { path: "/resources/:id", component: ResourceDetailView, props: true },
    { path: "/register", component: RegisterView },
    { path: "/resources/:id/versions", component: VersionsView, props: true },
    { path: "/resources/:id/reviews", component: ReviewsView, props: true },
    { path: "/resources/:id/dependencies", component: DependenciesView, props: true },
    { path: "/jobs", component: JobsView },
  ],
});

void App;
