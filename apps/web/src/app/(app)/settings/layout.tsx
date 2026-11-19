import { PageHeader } from "@/components/ui";
import { SettingsTabs } from "@/components/settings/tabs";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-5xl">
      <PageHeader title="Settings" subtitle="Your account and the active project" />
      <SettingsTabs />
      {children}
    </div>
  );
}
