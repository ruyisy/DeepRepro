import { ReactNode, useState } from 'react';
import Header from './Header';
import { TaskRecoveryBanner } from '../common/TaskRecoveryBanner';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { useTaskRecovery } from '../../hooks/useTaskRecovery';
import { useNavigationGuard } from '../../hooks/useNavigationGuard';

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const { isRecovering, recoveredTaskId } = useTaskRecovery();
  const [showBanner, setShowBanner] = useState(true);

  const {
    showConfirmDialog,
    confirmNavigation,
    cancelNavigation,
  } = useNavigationGuard();

  return (
    <div className="min-h-screen bg-[#020617] text-stone-950">
      {/* Task Recovery Banner */}
      {showBanner && (
        <TaskRecoveryBanner
          isRecovering={isRecovering}
          recoveredTaskId={recoveredTaskId}
          onDismiss={() => setShowBanner(false)}
        />
      )}

      {/* Navigation Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showConfirmDialog}
        title="Task is still running"
        message="A task is currently running. If you leave this page, the task will continue in the background, but you may lose track of its progress. Are you sure you want to leave?"
        confirmLabel="Leave"
        cancelLabel="Stay"
        variant="warning"
        onConfirm={confirmNavigation}
        onCancel={cancelNavigation}
      />

      <Header />
      <main className="relative overflow-hidden bg-[#020617]">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_5%,rgba(34,211,238,0.16),transparent_30%),radial-gradient(circle_at_88%_18%,rgba(124,58,237,0.18),transparent_28%),linear-gradient(180deg,#020617_0%,#07111f_45%,#f7efe0_100%)]" />
        <div className="pointer-events-none absolute inset-0 opacity-[0.08] [background-image:linear-gradient(rgba(125,211,252,0.45)_1px,transparent_1px),linear-gradient(90deg,rgba(125,211,252,0.45)_1px,transparent_1px)] [background-size:96px_96px]" />
        <div className="relative">{children}</div>
      </main>
    </div>
  );
}
