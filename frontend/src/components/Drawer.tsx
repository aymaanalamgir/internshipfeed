// Provide accessible modal drawers with brief slide transitions and focus restoration.
import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './Icon';

export function Drawer({ title, onClose, children, kind }: { title: string; onClose: () => void; children: ReactNode; kind: 'profile' | 'job' | 'apply' }) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const close = useRef(onClose);
  const closing = useRef(false);
  const [phase, setPhase] = useState<'opening' | 'open' | 'closing'>('opening');
  const animated = kind !== 'apply';
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const dialog = ref.current!;
    dialog.showModal();
    let frame = 0;
    if (animated && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(() => { if (!closing.current) setPhase('open'); });
      });
    } else setPhase('open');
    const onClosed = () => { if (!dialog.open) close.current(); };
    dialog.addEventListener('close', onClosed);
    return () => {
      cancelAnimationFrame(frame);
      dialog.removeEventListener('close', onClosed);
      dialog.close();
      if (opener?.isConnected) opener.focus();
      else document.querySelector<HTMLElement>('main h1')?.focus();
    };
  }, [animated]);
  useEffect(() => {
    if (phase !== 'closing') return;
    const dialog = ref.current!;
    const duration = Math.max(...getComputedStyle(dialog).transitionDuration.split(',').map(value => parseFloat(value) * (value.trim().endsWith('ms') ? 1 : 1000)));
    if (!animated || !duration || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      dialog.close();
      return;
    }
    const fallback = window.setTimeout(() => dialog.close(), duration + 50);
    return () => window.clearTimeout(fallback);
  }, [animated, phase]);
  function requestClose() {
    if (closing.current) return;
    closing.current = true;
    setPhase('closing');
  }
  return <dialog ref={ref} className={`drawer ${kind}-drawer`} data-state={phase} aria-labelledby={heading}
    onCancel={event => { event.preventDefault(); requestClose(); }}
    onTransitionEnd={event => {
      if (event.target === event.currentTarget && event.propertyName === 'transform' && closing.current) ref.current?.close();
    }}
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
    onClick={event => { if (event.target === event.currentTarget) requestClose(); }}>
    <div className="drawer-panel">
      <header className="drawer-header"><h2 id={heading} tabIndex={-1}>{title}</h2>
        <button className="icon-button" aria-label={`Close ${{ profile: 'profile', job: 'job details', apply: 'application confirmation' }[kind]}`} onClick={requestClose}><Icon name="close" /></button>
      </header>
      <div className="drawer-content">{children}</div>
    </div>
  </dialog>;
}
