/**
 * 비디오 프레임 썸네일 카드.
 *
 * src가 있으면 실제 이미지를 보여 주고, 없으면 seed로 정해지는 그라디언트 플레이스홀더를 그린다.
 * 오버레이는 재생 표지(누를 수 있을 때만), 오른쪽 아래 timecode, 왼쪽 위 label이다.
 */
import { useEffect, useState } from "react";
import { Play } from "lucide-react";

import { cn } from "@/shared/lib/cn";

/** seed 문자열로 0~359 범위의 hue를 만든다. 같은 seed는 항상 같은 값이 된다. */
function hashHue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (h * 31 + seed.charCodeAt(i)) % 360;
  }
  return h;
}

interface ThumbProps {
  /** 그라디언트 hue를 정하는 시드(클립 ID 등). */
  seed: string;
  /** 오른쪽 아래 타임코드 오버레이(예: "1:23"). */
  time?: string;
  /** 왼쪽 위 라벨 오버레이(예: 클립 번호). */
  label?: string;
  /** 카드 클릭 핸들러. 주면 카드를 `<button>`으로 렌더한다. */
  onClick?: () => void;
  /** 실제 썸네일 URL. 주면 그라디언트 대신 <img>를 렌더한다. */
  src?: string | null;
  className?: string;
}

/**
 * 썸네일 카드를 그린다. 실제 이미지나 seed로 정해지는 그라디언트 플레이스홀더를 보여 준다.
 *
 * 누를 수 있을 때만 `<button>`으로 렌더한다.
 * 항상 버튼으로 그리면 쓰는 곳마다 이름 없는 탭 정지가 생기고, 클립 목록에서는 줄마다 탭 정지가 붙는다.
 * 또 누를 수 있는 줄(`<button>`) 안에 버튼이 들어가 HTML 구조가 잘못된다. 누르면 미리보기가 열리는 곳만 `onClick`을 준다.
 */
export function Thumb({
  seed,
  time,
  label,
  onClick,
  src,
  className,
}: ThumbProps) {
  const hue = hashHue(seed || "x");
  // 이미지 로드가 실패하면(만료된 presigned URL 등) 깨진 이미지 아이콘 대신 그라디언트 placeholder를 그린다.
  const [imgFailed, setImgFailed] = useState(false);
  useEffect(() => {
    setImgFailed(false);
  }, [src]);
  const showImg = !!src && !imgFailed;

  // 누를 수 없는 썸네일은 그림이므로 탭 순서에 넣지 않는다.
  const Tag = onClick ? "button" : "div";

  return (
    <Tag
      {...(onClick ? { type: "button" as const, onClick } : {})}
      className={cn(
        // 모서리 둥글기는 의미 이름(rounded-control)으로 쓴다.
        // 숫자 단계(--radius-md)를 이름으로 가져오면 기준이 두 개가 된다.
        "relative w-full overflow-hidden rounded-control border-0 p-0",
        onClick ? "cursor-pointer" : "cursor-default",
        "aspect-video focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
        className,
      )}
      style={
        showImg
          ? undefined
          : {
              background: `linear-gradient(135deg, hsl(${hue} 30% 18%), hsl(${(hue + 40) % 360} 35% 10%))`,
            }
      }
    >
      {/* 실제 썸네일 이미지. 로드에 실패하면 onError에서 placeholder로 대체한다. */}
      {showImg && (
        <img
          src={src!}
          alt={label ?? seed}
          className="absolute inset-0 h-full w-full object-cover"
          draggable={false}
          onError={() => setImgFailed(true)}
        />
      )}

      {/*
        재생 표지는 누를 수 있는 썸네일에만 둔다. 누를 수 없는 그림에 두면 재생되는 것처럼 보이지만 눌러도 반응이 없다.
      */}
      {onClick && (
        <span className="thumb-play absolute inset-0 flex items-center justify-center text-white/55 transition-[color,background] duration-short hover:text-white/80">
          <Play size={26} fill="currentColor" strokeWidth={0} />
        </span>
      )}

      {/* 타임코드: 오른쪽 아래. */}
      {time && (
        <span
          className="absolute bottom-1.5 right-1.5 rounded-capsule px-1 py-px font-mono text-nano text-white/85"
          style={{ background: "rgba(0,0,0,.5)" }}
        >
          {time}
        </span>
      )}

      {/* 라벨: 왼쪽 위. */}
      {label && (
        <span
          className="absolute left-1.5 top-1.5 rounded-capsule px-1.5 py-px text-nano text-white/80"
          style={{ background: "rgba(0,0,0,.45)" }}
        >
          {label}
        </span>
      )}
    </Tag>
  );
}
