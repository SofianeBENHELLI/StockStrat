import { cn } from "@/lib/utils";
import { tone, usd } from "@/lib/lab";

export default function Money({
  value,
  signed = true,
  cents = false,
  className,
  neutral = false,
}: {
  value: number | null | undefined;
  signed?: boolean;
  cents?: boolean;
  className?: string;
  neutral?: boolean;
}) {
  return (
    <span className={cn("tabular-nums whitespace-nowrap", !neutral && tone(value), className)}>
      {usd(value, { signed, cents })}
    </span>
  );
}
