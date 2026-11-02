import { useEffect, useId, useRef } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './Icon';

export function Drawer({ title, onClose, children, kind }: { title: string; onClose: () => void; children: ReactNode; kind: 'profile' | 'job' }) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const dialog = ref.current!;
    dialog.showModal();
    const onClosed = () => { if (!dialog.open) close.current(); };
    dialog.addEventListener('close', onClosed);
    return () => {
      dialog.removeEventListener('close', onClosed);
      dialog.close();
      if (opener?.isConnected) opener.focus();
      else document.querySelector<HTMLElement>('main h1')?.focus();
    };
  }, []);
  return <dialog ref={ref} className={`drawer ${kind}-drawer`} aria-labelledby={heading}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const targets = [...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex]')]
        .filter(node => node.tabIndex >= 0 && !node.matches(':disabled') && node.getClientRects().length > 0);
      const first = targets[0], last = targets[targets.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !targets.includes(document.activeElement as HTMLElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !targets.includes(document.activeElement as HTMLElement))) {
        event.preventDefault(); first.focus();
      }
    }}
    onClick={event => { if (event.target === event.currentTarget) ref.current?.close(); }}>
    <div className="drawer-panel">
      <header className="drawer-header"><h2 id={heading} tabIndex={-1}>{title}</h2>
        <button className="icon-button" aria-label={`Close ${kind === 'profile' ? 'profile' : 'job details'}`} onClick={() => ref.current?.close()}><Icon name="close" /></button>
      </header>
      <div className="drawer-content">{children}</div>
    </div>
  </dialog>;
}
