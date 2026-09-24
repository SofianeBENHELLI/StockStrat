import { cn } from "@/lib/utils";
import { PROFILE_META, type ProfileKey } from "@/lib/lab";

const NAMES: Record<ProfileKey, string> = { flambeur: "Flambeur", matheux: "Matheux", stratege: "Stratège" };

export default function ProfileChip({ profile, className }: { profile: ProfileKey; className?: string }) {
  const m = PROFILE_META[profile];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ring-1", m.chip, className)}>
      <m.Icon className="size-3" />
      {NAMES[profile]}
    </span>
  );
}
