import { forwardRef, type ButtonHTMLAttributes } from 'react';

type PickerTriggerProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  open?: boolean;
};

export const PickerTrigger = forwardRef<HTMLButtonElement, PickerTriggerProps>(
  ({ open, className, style, children, ...rest }, ref) => {
    return (
      <button
        ref={ref}
        type="button"
        className={`flex items-center gap-1.5 px-2.5 h-8 min-w-0 max-w-full text-[13px] font-medium text-foreground bg-transparent transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer rounded-md ${className ?? ''}`}
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
