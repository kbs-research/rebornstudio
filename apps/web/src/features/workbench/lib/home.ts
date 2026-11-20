/**
 * 라벨링 워크벤치에서 돌아갈 곳과 읽기 전용 사유 문장.
 *
 * 작업자의 라벨링 큐는 `내 작업`에 있고, 관리자의 라벨링 큐는 작업 단계의 라벨링 화면에 있다.
 * 운영 역할에게 `내 작업`을 가리키면 그 사람이 할 일이 없는 화면으로 보내게 된다.
 * 머리 줄의 뒤로 가기, 읽기 전용 띠, 만료 띠, 제출 뒤 이동이 모두 이 값을 읽는다.
 * 작업을 놓은 뒤 토스트의 작은 줄(`afterReleaseHint`)도 여기에 둔다.
 */
import { withJosa } from "@/shared/lib/josa";

/** 워크벤치를 떠날 때 갈 화면. */
export interface WorkbenchHome {
  to: string;
  label: string;
}

/** 운영 역할의 목적지 이름. 작업 단계 스텝퍼의 라벨링 항목과 같은 이름이다. */
const MANAGER_LABEL = "라벨링";

/**
 * 라벨링 워크벤치의 돌아갈 곳을 정한다.
 *
 * 운영 역할이라도 프로젝트 주소(slug)를 아직 받지 못했으면 `내 작업`으로 둔다.
 * 틀린 주소로 보내지 않고 항상 열리는 화면으로 보낸다.
 *
 * @param manages 이 프로젝트의 관리자(최고관리자 포함)이면 true.
 * @param slug 작업이 속한 프로젝트의 주소 이름.
 */
export function labelWorkbenchHome(manages: boolean, slug: string | null | undefined): WorkbenchHome {
  if (manages && slug) return { to: `/p/${slug}/pipeline?stage=label`, label: MANAGER_LABEL };
  return { to: "/my/work", label: "내 작업" };
}

/**
 * 제출·신고로 작업을 놓은 뒤 토스트의 작은 줄을 만든다. 굵은 줄은 놓은 방법(`작업 1건 제출` 등)이다.
 *
 * 두 동작이 같은 문장을 쓰므로 놓은 방법을 다시 쓰지 않고 이동할 곳만 쓴다.
 * 신고 뒤에 `냈습니다`라고 쓰면 제출하지 않은 작업을 낸 것으로 읽힌다.
 *
 * @param homeLabel 돌아갈 화면의 이름(`labelWorkbenchHome`의 `label`).
 */
export function afterReleaseHint(homeLabel: string): string {
  return `${withJosa(homeLabel, "으로", "로")} 돌아갑니다.`;
}

/** 읽기 전용 사유를 고르는 데 쓰는 작업 값. */
interface ReadOnlyInput {
  status: string;
  claimed_by?: string | null;
}

/**
 * 편집할 수 없는 작업의 사유 문장을 만든다.
 *
 * 보유 여부는 `status` 대신 `claimed_by`로 판정한다. `claimed`인데 보유자가 비어 있는 작업이 있다.
 * 제출 문장은 보는 사람에 따라 주어가 다르다. 라벨러에게는 자기가 낸 작업이고, 운영 역할에게는 다른 사람이 낸 작업이다.
 *
 * @param item 작업의 상태와 보유자.
 * @param manages 관리자이면 true.
 */
export function readOnlyReason(item: ReadOnlyInput, manages: boolean): string {
  switch (item.status) {
    case "submitted":
      return manages
        ? "제출된 작업은 검수가 끝날 때까지 고칠 수 없습니다."
        : "제출한 작업은 검수가 끝날 때까지 고칠 수 없습니다.";
    case "approved":
      return "승인된 작업은 고칠 수 없습니다.";
    case "cancelled":
      return "종료된 작업은 고칠 수 없습니다.";
    default:
      return item.status === "claimed" && item.claimed_by
        ? "다른 사람이 맡은 작업입니다. 편집은 맡은 사람만 합니다."
        : "아직 아무도 맡지 않은 작업입니다. 편집은 다음 작업으로 받은 사람만 합니다.";
  }
}
