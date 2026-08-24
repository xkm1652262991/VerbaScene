import type { ReactNode } from "react";

import { StageNav, type WorkflowStepView } from "./StageNav";

type StudioShellProps = {
  activeStep: {
    id: string;
    label: string;
    summary: string;
  };
  children: ReactNode;
  completedCount: number;
  completionPercent: number;
  extraActions?: ReactNode;
  meta: string[];
  onSelectStep: (stepId: string) => void;
  sidebar: ReactNode;
  steps: WorkflowStepView[];
  title: string;
  totalCount: number;
};

export function StudioShell({
  activeStep,
  children,
  completedCount,
  completionPercent,
  extraActions,
  meta,
  onSelectStep,
  sidebar,
  steps,
  title,
  totalCount,
}: StudioShellProps) {
  return (
    <div className="studio-workbench">
      <header className="studio-topbar compact">
        <div className="studio-title-area">
          <a className="icon-link" href="#/projects" aria-label="返回项目列表">
            ←
          </a>
          <i className="studio-clapper" aria-hidden="true" />
          <div>
            <div className="studio-kicker">ANIMATED ENGLISH · PRODUCTION</div>
            <h1>{title}</h1>
            <div className="studio-meta">
              {meta.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
          </div>
        </div>
        <div className="studio-topbar-actions">
          <div className="studio-progress-pill">
            <strong>
              {completedCount}/{totalCount}
            </strong>
            <span>流程完成</span>
          </div>
          {extraActions}
        </div>
      </header>

      <StageNav
        activeStepId={activeStep.id}
        completionPercent={completionPercent}
        onSelectStep={onSelectStep}
        steps={steps}
      />

      <main className="workflow-main">
        <section className="workflow-current">
          <div className="workflow-context-bar">
            <div className="workflow-current-aside">
              <span>NOW ON STAGE</span>
              <strong>{activeStep.label}</strong>
              {activeStep.summary !== activeStep.label ? <p>{activeStep.summary}</p> : null}
            </div>
            <details className="workflow-run-drawer">
              <summary>运行记录</summary>
              {sidebar}
            </details>
          </div>
          <div className="workflow-current-body">{children}</div>
        </section>
      </main>
    </div>
  );
}
