"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

interface UserProfile {
  is_api_key: boolean;
  user?: { id: string; email: string; name: string };
  current_project?: { id: string; name: string; role: string };
  project?: { id: string; name: string; role: string };
  projects?: Array<{ id: string; name: string; role: string }>;
}

export function UserNav() {
  const router = useRouter();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetch("/api/proxy/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (active) {
          setProfile(data);
          setLoading(false);
        }
      })
      .catch(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function handleLogout() {
    document.cookie = "matrix_session=; path=/; max-age=0";
    document.cookie = "matrix_project_id=; path=/; max-age=0";
    router.push("/login");
    router.refresh();
  }

  function handleSwitchProject(projectId: string) {
    document.cookie = `matrix_project_id=${projectId}; path=/; max-age=${72 * 3600}; SameSite=Lax`;
    window.location.reload();
  }

  if (loading) {
    return <div className="mt-auto px-2 py-3 text-[11px] text-zinc-600">Loading auth...</div>;
  }

  if (!profile || (profile.is_api_key && !profile.user)) {
    return (
      <div className="mt-auto border-t border-zinc-800/80 pt-3">
        <Link
          href="/login"
          className="flex items-center justify-center rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800/80 hover:text-zinc-100"
        >
          Sign In
        </Link>
      </div>
    );
  }

  const user = profile.user;
  const currentProj = profile.current_project || profile.project;
  const role = currentProj?.role ?? "viewer";

  const roleStyle =
    role === "admin"
      ? "bg-violet-950/60 text-violet-300 border-violet-800/60"
      : role === "member"
        ? "bg-sky-950/60 text-sky-300 border-sky-800/60"
        : "bg-zinc-800 text-zinc-400 border-zinc-700";

  return (
    <div className="mt-auto border-t border-zinc-800/80 pt-3 text-xs">
      {user && (
        <div className="mb-2 px-1">
          <div className="truncate font-medium text-zinc-200">{user.name}</div>
          <div className="truncate text-[10px] text-zinc-500">{user.email}</div>
        </div>
      )}

      {currentProj && (
        <div className="mb-2 rounded border border-zinc-800/60 bg-zinc-950/80 p-1.5">
          <div className="mb-1 flex items-center justify-between text-[10px] text-zinc-400">
            <span>Project</span>
            <span className={`rounded border px-1 py-0.2 font-mono uppercase text-[9px] ${roleStyle}`}>
              {role}
            </span>
          </div>

          {profile.projects && profile.projects.length > 1 ? (
            <select
              value={currentProj.id}
              onChange={(e) => handleSwitchProject(e.target.value)}
              className="w-full rounded border border-zinc-800 bg-zinc-900 px-1.5 py-1 text-[11px] text-zinc-200 focus:outline-none"
            >
              {profile.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.role})
                </option>
              ))}
            </select>
          ) : (
            <div className="truncate text-[11px] font-medium text-zinc-300">{currentProj.name}</div>
          )}
        </div>
      )}

      <button
        onClick={handleLogout}
        className="w-full rounded py-1 text-left text-[11px] text-zinc-500 hover:text-red-400"
      >
        Sign Out
      </button>
    </div>
  );
}
