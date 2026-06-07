import { forwardRef, type ButtonHTMLAttributes } from 'react';

type PickerVariant = 'bordered' | 'footer';

type PickerTriggerProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  open?: boolean;
  /**
   * `bordered` (default): the header / session pickers — 36px, bordered box,
   * 13px medium body text, accent border on open.
   * `footer`: the StatusBar pickers — compact Label typography (Geist Mono
   * 10px, 0.12em tracking, uppercase), subtle panel-2 rest / panel-3 hover,
   * no border. Anchored by the `picker-footer` class (CSS handles hover so we
   * don't need JS hover state).
   */
  variant?: PickerVariant;
};

export const PickerTrigger = forwardRef<HTMLButtonElement, PickerTriggerProps>(
  ({ open, variant = 'bordered', className, style, children, ...rest }, ref) => {
    if (variant === 'footer') {
      return (
        <button
          ref={ref}
          type="button"
          className={`picker-footer flex items-center gap-1 px-2 h-6 min-w-0 max-w-full font-mono uppercase bg-transparent transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer rounded-sm ${className ?? ''}`}
          style={{
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '0.12em',
            color: 'var(--color-text-dim)',
            backgroundColor: open ? 'var(--color-panel-3)' : 'var(--color-panel-2)',
            ...style,
          }}
          {...rest}
        >
          {children}
        </button>
      );
    }

    return (
      <button
        ref={ref}
        type="button"
        className={`flex items-center gap-1.5 px-2.5 h-9 min-w-0 max-w-full text-[13px] font-medium text-foreground bg-transparent transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer rounded-md ${className ?? ''}`}
        style={{
          border: `1px solid ${open ? 'color-mix(in oklab, var(--color-accent) 33%, transparent)' : 'var(--color-border)'}`,
          ...style,
        }}
        {...rest}
      >
        {children}
      </button>
    );
  },
);
PickerTrigger.displayName = 'PickerTrigger';
