/**
 * 식별자를 짧게 보여 주고, 누르면 전체를 복사하는 칩.
 *
 * 표의 한 열에 36자를 넣으면 다른 열을 모두 밀어내므로 화면에는 UUID 앞 8자만 보여 준다.
 * 운영에서는 그 id로 로그와 DB를 찾는데, 8자 접두어로는 `LIKE 'a1b2c3d4%'`처럼 앞부분 일치로만 찾을 수 있다.
 * 그래서 보이는 값은 짧게 두고 복사되는 값은 전체로 둔다.
 *
 * 표 안에서 쓰므로 클릭이 행으로 전달되지 않게 막는다.
 * 막지 않으면 `audit_log`처럼 행 클릭이 있는 표에서 id를 누를 때 상세가 함께 열린다.
 *
 * 복사는 `copyText`가 해서, 평문 http 주소처럼 클립보드 API가 없는 곳에서도 복사한다.
 * 복사 실패도 알린다. 알림 없이 실패하면 사용자는 붙여넣기를 해 본 뒤에야 실패를 알 수 있다.
 */
import { Copy } from "lucide-react";

import { copyText } from "@/shared/lib/clipboard";
import { cn } from "@/shared/lib/cn";
import { eulReul } from "@/shared/lib/josa";
import { useToast } from "./Toast";

/** 식별자 앞부분을 보여 주는 버튼을 그린다. 누르면 전체 값을 클립보드에 복사한다. */
export function IdChip({
  id,
  label = "ID",
  chars = 8,
  className,
  tabIndex,
}: {
  /** 전체 식별자. 이 값이 복사된다. */
  id: string;
  /** 어떤 대상의 id인지 나타내는 이름. 토스트 문구와 스크린리더 라벨에 쓴다. */
  label?: string;
  /** 화면에 보여 줄 앞 글자 수. */
  chars?: number;
  className?: string;
  /** 탭 순서. 목록이 고른 행에만 탭 정지를 두는 곳(로빙 tabindex)에서 `-1`을 준다. */
  tabIndex?: number;
}) {
  const { toast } = useToast();

  const copy = (e: React.MouseEvent) => {
    // 행 클릭이 있는 표에서 상세가 함께 열리지 않게 한다.
    e.stopPropagation();
    copyText(id).then(
      () => toast("success", `${eulReul(label)} 복사했습니다`, id),
      // 마우스를 올리면 `title`로 전체 값이 보인다. 복사가 막힌 브라우저에서 값을 얻는 방법이다.
      () => toast("error", `${label} 복사 실패`, "마우스를 올리면 전체 값이 보입니다."),
    );
  };

  return (
    <button
      type="button"
      onClick={copy}
      // 마우스를 올리면 전체 값을 보여 준다. 복사하지 않고 눈으로만 대조할 때가 있다.
      title={id}
      tabIndex={tabIndex}
      aria-label={`${label} 전체 복사 (${id})`}
      className={cn(
        "group inline-flex items-center gap-1 rounded-field px-1 py-0.5 font-mono text-caption text-label-3",
        "transition-colors hover:bg-fill hover:text-label-2",
        className,
      )}
    >
      {id.slice(0, chars)}
      {/* 복사 아이콘은 hover 때만 보인다. 표의 모든 행에 항상 보이면 그 열이 복잡해진다. */}
      <Copy
        size={12}
        aria-hidden="true"
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
      />
    </button>
  );
}
