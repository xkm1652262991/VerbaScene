export type WorkflowStepView = {
  id: string;
  label: string;
  status: string;
  ready: boolean;
  canEnter: boolean;
};

type StageNavProps = {
  activeStepId: string;
  completionPercent: number;
  onSelectStep: (stepId: string) => void;
  steps: WorkflowStepView[];
};

export function StageNav({ activeStepId, completionPercent, onSelectStep, steps }: StageNavProps) {
  return (
    <nav className="workflow-progress" aria-label="项目制作流程">
      <div className="workflow-progress-line" aria-hidden="true">
        <span style={{ width: `${completionPercent}%` }} />
      </div>
      {steps.map((step, index) => {
        const isActive = step.id === activeStepId;
        const isDone = step.ready;
        const isReachable = isActive || isDone || step.canEnter;
        return (
          <button
            className={isActive ? "workflow-step active" : isDone ? "workflow-step done" : "workflow-step"}
            disabled={!isReachable}
            key={step.id}
            onClick={() => onSelectStep(step.id)}
            type="button"
          >
            <span className="workflow-step-index">{String(index + 1).padStart(2, "0")}</span>
            <span className="workflow-step-copy">
              <strong>{step.label}</strong>
              <em>{step.status}</em>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
