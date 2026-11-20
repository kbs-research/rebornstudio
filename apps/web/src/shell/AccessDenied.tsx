/**
 * 막힌 화면의 공통 안내. 왜 막혔는지와 어디로 가면 되는지를 보여 준다.
 *
 * 빈 화면이나 raw 에러 대신 이 패널을 그린다.
 * 막힌 까닭(`reason`)이 머리글과 기본 문구를 정한다.
 * 버튼은 그 사람 역할의 첫 화면으로 보낸다. 첫 화면은 `lib/roles`의 `landingFor`가 정하고, 홈(`/`)도 같은 표를 읽는다.
 * 누가 여는 화면인지와 어디로 가야 하는지가 빠진 한 가지 문구로는 막힌 사람이 할 일을 알 수 없다.
 */
import { Lock } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { buttonClasses } from "@/shared/ui/Button";
import { StatePanel } from "@/shared/ui/StatePanel";
import { useProjects } from "@/features/projects";
import { type ProjectDTO } from "@/shared/api/client";
import { PROJECT_ROLE_LABEL } from "@/lib/role_label";
import {
  type Destination,
  isSuperAdmin,
  landingFor,
  moveLabel,
  PROJECT_LIST_LANDING,
} from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";

/**
 * 막힌 까닭.
 *
 * - `role`: 프로젝트 역할로 이 화면을 열 수 없는 경우.
 * - `global`: 플랫폼 역할(최고관리자)이 필요한 경우.
 * - `not-member`: 주소의 프로젝트가 목록에 없거나 멤버가 아닌 경우.
 * - `no-project`: 주소에 프로젝트가 없는 화면인데 고른 프로젝트도 없는 경우.
 * - `not-holder`: 호출한 사용자가 이 작업을 보유하지 않은 경우. 워크벤치가 서버 사유와 함께 쓴다.
 */
export type DeniedReason = "role" | "global" | "not-member" | "no-project" | "not-holder";

/** 까닭별 기본 문구. `message`는 첫 문장, `hint`는 다음 할 일이다. */
const DEFAULT_COPY: Record<DeniedReason, { message: string; hint: string | null }> = {
  role: {
    message: "이 프로젝트에서 이 화면을 볼 권한이 없습니다.",
    hint: "권한이 필요하면 프로젝트 관리자에게 요청해주세요.",
  },
  global: {
    message: "최고관리자만 볼 수 있는 화면입니다.",
    hint: "권한이 필요하면 최고관리자에게 요청해주세요.",
  },
  "not-member": {
    message: "프로젝트를 찾을 수 없거나 이 프로젝트의 멤버가 아닙니다.",
    hint: "참여가 필요하면 프로젝트 관리자에게 요청해주세요.",
  },
  "no-project": {
    message: "프로젝트 전환기에서 프로젝트를 고르면 이 화면이 열립니다.",
    hint: null,
  },
  "not-holder": {
    message: "이 작업을 보유 중인 사람만 열 수 있습니다.",
    hint: null,
  },
};

interface Props {
  /** 막힌 까닭. 기본은 `role`. */
  reason?: DeniedReason;
  /**
   * 첫 문장. 없으면 까닭의 기본 문구를 쓴다.
   * 첫 문장만 넘기면 기본 안내 줄을 붙이지 않는다. 넘긴 문장에 안내가 이미 들어 있는 호출부가 있다.
   */
  message?: string;
  /** 다음 할 일 한 문장. `null`이면 안내 줄을 그리지 않는다. */
  hint?: string | null;
  /** 버튼 목적지. 없으면 역할의 첫 화면으로 보낸다. */
  to?: Destination;
  /** 첫 화면을 정할 프로젝트. 없으면 고른 프로젝트를 쓴다. */
  project?: ProjectDTO;
}

/** 권한 없음 안내 패널을 그린다. 머리글 · 첫 문장 · 안내 · 이동 버튼 순서다. */
export function AccessDenied({ reason = "role", message, hint, to, project }: Props) {
  const { user } = useAuth();
  const { selectedProjectId } = useSelectedProject();
  const { pathname, search } = useLocation();
  const projects = useProjects({ enabled: !!user && !project });
  // 목록 조회가 실패하면 역할을 알 수 없으므로 첫 화면을 짐작하지 않고 누구나 여는 프로젝트 목록으로 보낸다.
  const context = project
    ?? (projects.isError ? undefined : projects.data?.find((p) => p.project_id === selectedProjectId));

  const base = DEFAULT_COPY[reason];
  const text = message ?? base.message;
  // 첫 문장만 넘긴 호출부는 자기 문장에 안내를 담았다. 기본 안내를 덧붙이면 안내가 두 번 나온다.
  const guide = hint !== undefined ? hint : message !== undefined ? null : base.hint;

  const listFirst = reason === "not-member" || reason === "no-project";
  let dest: Destination = to
    ?? (listFirst ? PROJECT_LIST_LANDING : landingFor(context?.my_role, isSuperAdmin(user)));
  // 목적지가 지금 주소와 같으면 버튼을 눌러도 화면이 바뀌지 않으므로 프로젝트 목록으로 보낸다.
  if (dest.to === `${pathname}${search}`) dest = PROJECT_LIST_LANDING;

  // 사용자의 역할을 사실 줄로 보여 준다. 누가 여는 화면인지는 첫 문장에 이미 있으므로 되풀이하지 않는다.
  const mine = reason === "role" && context?.my_role ? (PROJECT_ROLE_LABEL[context.my_role] ?? context.my_role) : null;

  return (
    <StatePanel
      icon={<Lock size={18} aria-hidden="true" />}
      title={reason === "no-project" ? "프로젝트 선택 필요" : "접근 권한 없음"}
      lead={text}
      hint={guide}
      facts={mine ? [{ label: "내 역할", value: mine }] : undefined}
      actions={
        <Link to={dest.to} className={buttonClasses("primary")}>
          {moveLabel(dest.label)}
        </Link>
      }
    />
  );
}
