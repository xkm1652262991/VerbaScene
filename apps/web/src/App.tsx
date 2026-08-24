import { useEffect, useState } from "react";

import { ModelManagementPage } from "./pages/ModelManagementPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectListPage } from "./pages/ProjectListPage";
import { TaskCenterPage } from "./pages/TaskCenterPage";

type Route =
  | { name: "projects" }
  | { name: "create" }
  | { name: "project"; projectId: string }
  | { name: "assetGeneration"; projectId: string }
  | { name: "models" }
  | { name: "shot"; projectId: string; shotId: string }
  | { name: "tasks"; taskId?: string; projectId?: string };

function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  const [path, search = ""] = hash.split("?");
  const params = new URLSearchParams(search);

  if (path === "/projects" || path === "") {
    return { name: "projects" };
  }

  if (path === "/projects/new") {
    return { name: "create" };
  }

  if (path === "/tasks") {
    return {
      name: "tasks",
      taskId: params.get("task_id") ?? undefined,
      projectId: params.get("project_id") ?? undefined,
    };
  }

  if (path === "/models") {
    return { name: "models" };
  }

  const assetGenerationMatch = path.match(/^\/projects\/([^/]+)\/assets\/generate$/);
  if (assetGenerationMatch) {
    return { name: "assetGeneration", projectId: assetGenerationMatch[1] };
  }

  const assetsMatch = path.match(/^\/projects\/([^/]+)\/assets$/);
  if (assetsMatch) {
    return { name: "project", projectId: assetsMatch[1] };
  }

  const filesMatch = path.match(/^\/projects\/([^/]+)\/files$/);
  if (filesMatch) {
    return { name: "project", projectId: filesMatch[1] };
  }

  const shotMatch = path.match(/^\/projects\/([^/]+)\/shots\/([^/]+)$/);
  if (shotMatch) {
    return { name: "shot", projectId: shotMatch[1], shotId: shotMatch[2] };
  }

  const projectMatch = path.match(/^\/projects\/([^/]+)$/);
  if (projectMatch) {
    return { name: "project", projectId: projectMatch[1] };
  }

  return { name: "projects" };
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const isImmersiveProject = route.name === "project" || route.name === "shot" || route.name === "assetGeneration";

  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return (
    <main className={`app-shell route-${route.name}${isImmersiveProject ? " immersive-project" : ""}`}>
      {!isImmersiveProject ? <WorkspaceNav route={route} /> : null}
      <section className="workspace">
        {route.name === "projects" ? <ProjectListPage /> : null}
        {route.name === "create" ? <ProjectListPage autoFocusCreation /> : null}
        {route.name === "tasks" ? <TaskCenterPage initialProjectId={route.projectId} initialTaskId={route.taskId} /> : null}
        {route.name === "models" ? <ModelManagementPage /> : null}
        {route.name === "shot" ? <ProjectDetailPage initialShotId={route.shotId} projectId={route.projectId} /> : null}
        {route.name === "assetGeneration" ? <ProjectDetailPage initialWorkspace="asset-generation" projectId={route.projectId} /> : null}
        {route.name === "project" ? (
          <ProjectDetailPage projectId={route.projectId} />
        ) : null}
      </section>
    </main>
  );
}

function WorkspaceNav({ route }: { route: Route }) {
  const projectId = "projectId" in route ? route.projectId : null;
  const isProjectArea = Boolean(projectId);
  return (
    <aside className="workspace-nav" aria-label="工作台导航">
      <a className="workspace-brand" href="#/projects">
        <i className="workspace-brand-mark" aria-hidden="true" />
        <span className="workspace-brand-copy">
          <small>VERBASCENE</small>
          <strong>语境片场</strong>
        </span>
      </a>
      <nav className="workspace-global-nav">
        <span>全局</span>
        <a className={route.name === "projects" || route.name === "create" ? "active" : ""} href="#/projects">
          项目列表
        </a>
        <a className={route.name === "tasks" ? "active" : ""} href="#/tasks">
          任务中心
        </a>
        <a className={route.name === "models" ? "active" : ""} href="#/models">
          模型管理
        </a>
      </nav>
      <nav className="workspace-project-nav">
        <span>当前项目</span>
        {projectId ? (
          <>
            <a className={route.name === "project" || route.name === "shot" ? "active" : ""} href={`#/projects/${projectId}`}>
              剧本 · 资产库 · 视频制作
            </a>
          </>
        ) : (
          <p>{isProjectArea ? "" : "打开项目后显示项目内导航。"}</p>
        )}
      </nav>
      <div className="workspace-live" aria-label="工作台状态">
        <i aria-hidden="true" />
        LOCAL STUDIO
      </div>
    </aside>
  );
}
