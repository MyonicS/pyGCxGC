import { cn } from '@/lib/utils'

export function Kbd({ className, ...props }: React.ComponentProps<'kbd'>) {
  return (
    <kbd
      className={cn(
        'ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-sm border border-current/25 px-1 font-mono text-[10px] leading-none opacity-80',
        className,
      )}
      {...props}
    />
  )
}
