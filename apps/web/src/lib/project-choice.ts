// Project opened by a fresh login (pure; see app/api/auth/login).
import { PROJECT_ID_RE } from "./session-cookie";
import type { Membership } from "./types";

export type ProjectChoice = { projectId: string | null; next: string | null; notice: string | null };

/**
 * Which project a fresh login opens. NEVER the highest role: anybody can add a victim as owner of
 * their own project, which would then capture the victim's default. Order:
 *  1. the project this user last used on this browser (persistent, user-bound cookie), or the
 *     still-present project cookie, when the user still belongs to it;
 *  2. the only project, when there is exactly one (with a notice if the user used another before);
 *  3. with two or more and nothing known: no project, the /projects picker, and a notice.
 */
export function chooseProject(memberships: Membership[], last: string | null, current: string | null): ProjectChoice {
  const usable = memberships.filter((m) => m && m.project && PROJECT_ID_RE.test(m.project.id));
  const known = usable.find((m) => m.project.id === last) ?? usable.find((m) => m.project.id === current);
  if (known) return { projectId: known.project.id, next: null, notice: null };
  const hadPrevious = Boolean(last || current);
  if (usable.length === 1) {
    const only = usable[0].project;
    return { projectId: only.id, next: null, notice: hadPrevious ? `You were added to project "${only.name}".` : null };
  }
  if (usable.length === 0) return { projectId: null, next: null, notice: null };
  return {
    projectId: null,
    next: "/projects",
    notice: hadPrevious
      ? "Your previous project is not available. Choose a project; check that you know each one listed, since you may have been added to new ones."
      : "Choose a project to open; check that you know each one listed, since you may have been added to new ones.",
  };
}
