/**
 * 전역 토스트 알림(success, error, warning, info).
 *
 * ToastProvider를 앱 루트에 두고 useToast()로 어디서든 부른다.
 * 5초 뒤 자동으로 사라지고 최대 5개까지 쌓인다.
 * 흑연색 면 위에 종류 점 하나, 굵은 결과, 옅은 안내를 한 줄로 둔다.
 * 면은 `data-surface="graphite"`라 종이·흑연 어느 화면 위에서도 같은 모양이다.
 */
import { X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

import { Chip } from "./Chip";

type ToastType = "success" | "error" | "warning" | "info";

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
  detail?: string;
  /**
   * 같은 알림이 들어온 횟수. 1이면 화면에 쓰지 않는다.
   *
   * 같은 문구가 올 때마다 줄을 쌓으면 재시도할 때마다 같은 사실을 한 번 더 알리고,
   * 다섯 줄뿐인 공간을 같은 알림으로 채워 다른 알림을 밀어낸다.
   */
  count: number;
  leaving?: boolean;
}

/** 퇴장 슬라이드아웃 시간(ms). globals.css의 --duration-normal과 맞춘다. */
const LEAVE_MS = 240;

interface ToastCtx {
  toast: (type: ToastType, message: string, detail?: string) => void;
}

/** 토스트 함수를 나눠 주는 컨텍스트. Provider 밖의 기본값은 아무 동작도 하지 않는 함수다. */
const Ctx = createContext<ToastCtx>({ toast: () => {} });

/**
 * 종류별 점 색과 읽는 이름.
 *
 * `title`은 화면에 그리지 않고 스크린리더가 알림 종류를 읽는 데만 쓴다.
 * 굵은 줄에는 결과를, 작은 줄에는 안내를 쓴다. 한 문장에 결과와 다음 할 일을 같이 넣지 않는다.
 */
const TYPE: Record<ToastType, { title: string; dot: string }> = {
  success: { title: "완료", dot: "bg-ok" },
  error: { title: "오류", dot: "bg-bad" },
  warning: { title: "주의", dot: "bg-warn" },
  info: { title: "알림", dot: "bg-note" },
};

let _nextId = 0;

/** 토스트 상태를 관리하고 화면 오른쪽 아래에 알림 목록을 그린다. 같은 알림은 한 줄로 합친다. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  /*
    지금 떠 있는 알림 문구와 id의 대응표. 상태(`items`)는 렌더 뒤에야 갱신되므로
    같은 틱에 세 번 오면 세 번 모두 새 줄이 된다. 이 대응표는 `toast()` 안에서 바로 갱신되어 그 경우에도 한 줄로 합친다.
  */
  const live = useRef(new Map<string, number>());
  const keyOf = (type: ToastType, message: string, detail?: string) =>
    `${type}\u0000${message}\u0000${detail ?? ""}`;
  /** id별 자동 퇴장 타이머. 같은 알림이 다시 오면 방금 일어난 일이므로 타이머를 다시 시작한다. */
  const timers = useRef(new Map<number, number>());

  /** 알림을 내보낸다. leaving 표시로 슬라이드아웃을 재생하고 LEAVE_MS 뒤 DOM에서 제거한다. */
  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id);
    if (t) {
      clearTimeout(t);
      timers.current.delete(id);
    }
    // 나가는 줄의 문구는 대응표에서 뺀다. 그래야 같은 알림이 다시 오면 새 줄로 뜬다.
    for (const [k, v] of live.current) if (v === id) live.current.delete(k);
    setItems((prev) => prev.map((x) => (x.id === id ? { ...x, leaving: true } : x)));
    setTimeout(() => setItems((prev) => prev.filter((x) => x.id !== id)), LEAVE_MS);
  }, []);

  const toast = useCallback(
    (type: ToastType, message: string, detail?: string) => {
      const arm = (id: number) => {
        const prev = timers.current.get(id);
        if (prev) clearTimeout(prev);
        timers.current.set(id, window.setTimeout(() => dismiss(id), 5000));
      };
      /*
        같은 알림은 새 줄을 쌓는 대신 횟수를 올리고 타이머를 다시 시작해 한 줄로 합친다.
        세 번 일어난 일을 한 줄에 횟수로 보여 주면 같은 문장을 세 줄로 늘어놓는 것보다 정확하고 공간도 덜 차지한다.
        사라지던 알림이 다시 살아나면 어색하므로 사라지는 중인 줄에는 합치지 않는다.
      */
      const key = keyOf(type, message, detail);
      const dup = live.current.get(key);
      if (dup != null) {
        setItems((prev) => prev.map((t) => (t.id === dup ? { ...t, count: t.count + 1 } : t)));
        arm(dup);
        return;
      }
      const id = ++_nextId;
      live.current.set(key, id);
      setItems((prev) => {
        // 다섯 줄을 넘어 밀려나는 알림의 문구도 대응표에서 뺀다.
        const over = prev.length - 4;
        if (over > 0) for (const t of prev.slice(0, over)) live.current.delete(keyOf(t.type, t.message, t.detail));
        return [...prev.slice(-4), { id, type, message, detail, count: 1 }];
      });
      arm(id); // 5초 뒤 오른쪽으로 슬라이드아웃한다.
    },
    [dismiss],
  );

  /* window 이벤트로 React 밖(queryClient의 전역 뮤테이션 onError 등)에서 보낸 토스트를 받는다.
     React 밖에서는 훅을 부를 수 없으므로 `reborn:toast` 이벤트로 넘긴다. */
  useEffect(() => {
    const handler = (e: Event) => {
      const d = (e as CustomEvent).detail as
        | { type?: ToastType; message: string; detail?: string }
        | undefined;
      if (d?.message) toast(d.type ?? "error", d.message, d.detail);
    };
    window.addEventListener("reborn:toast", handler);
    return () => window.removeEventListener("reborn:toast", handler);
  }, [toast]);

  return (
    <Ctx.Provider value={{ toast }}>
      {children}
      {/* 토스트 목록. 화면 오른쪽 아래에 고정하고 위로 쌓는다. */}
      <div
        data-surface="graphite"
        className="pointer-events-none fixed bottom-4 right-4 z-toast flex flex-col-reverse gap-2"
        aria-live="polite"
      >
        {items.map((t) => {
          const { title, dot } = TYPE[t.type];
          return (
            <div
              key={t.id}
              role="alert"
              className={cn(
                "group pointer-events-auto flex w-[400px] max-w-[calc(100vw-2rem)] items-start gap-3",
                "rounded-card border border-separator bg-canvas px-4 py-3 shadow-pop",
                t.leaving ? "animate-slide-out-right" : "animate-slide-in-right",
              )}
            >
              <span aria-hidden="true" className={cn("mt-2 size-2 shrink-0 rounded-capsule", dot)} />
              <div className="min-w-0 flex-1">
                {/* 굵은 줄에는 결과를 쓴다. 종류는 아이콘과 색이 이미 보여 주므로 글자로 다시 쓰지 않는다.
                    줄바꿈은 body의 keep-all이 처리한다. break-words를 주면 keep-all 설정을 되돌린다. */}
                <p className="text-callout font-semibold text-label">
                  <span className="sr-only">{title}: </span>
                  {t.message}
                  {/* 합친 횟수는 1을 넘을 때만 쓴다. 항상 붙어 있으면 눈에 띄지 않는다. */}
                  {/* 앞에 공백을 둔다. 없으면 스크린리더가 `제출2회`처럼 붙여 읽는다. */}
                  {t.count > 1 && " "}
                  {t.count > 1 && (
                    <Chip variant="count" className="ml-1 align-middle">
                      {t.count}회
                    </Chip>
                  )}
                </p>
                {t.detail && (
                  <p className="mt-0.5 text-footnote text-label-2">{t.detail}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismiss(t.id)}
                // 알림이 여럿 쌓이면 `닫기`만으로는 어느 알림인지 알 수 없으므로 결과 문구를 붙인다.
                aria-label={`알림 닫기: ${t.message}`}
                className="-mr-1.5 -mt-1 inline-flex size-6 shrink-0 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

/** 토스트 함수를 돌려준다. 어디서든 toast("success", "저장 완료")처럼 부른다. */
export function useToast() {
  return useContext(Ctx);
}
