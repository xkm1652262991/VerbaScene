import type { ReactNode } from "react";

export type StageAction = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  reason?: string | null;
};

type StageActionBarProps = {
  primary?: StageAction | null;
  secondary?: StageAction[];
  messages?: Array<string | null | undefined>;
  children?: ReactNode;
};

export function StageActionBar({
  children,
  messages = [],
  primary,
  secondary = [],
}: StageActionBarProps) {
  const visibleMessages = messages.filter((message): message is string => Boolean(message?.trim()));

  return (
    <div className="stage-action-bar">
      <div className="stage-action-buttons">
        {secondary.map((action) => (
          <button
            className="secondary-button"
            disabled={action.disabled}
            key={action.label}
            onClick={action.onClick}
            type="button"
          >
            {action.label}
          </button>
        ))}
        {children}
        {primary ? (
          <button
            className="primary-button"
            disabled={primary.disabled}
            onClick={primary.onClick}
            type="button"
          >
            {primary.label}
          </button>
        ) : null}
      </div>
      {visibleMessages.length > 0 ? (
        <div className="stage-action-reasons" role="note">
          {visibleMessages.map((message) => (
            <p key={message}>{message}</p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
