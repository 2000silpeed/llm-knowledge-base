"use client";

import { useEffect } from "react";

export default function SwRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/sw.js")
        .catch(() => {
          // SW 등록 실패는 무시 (앱 동작에 영향 없음)
        });
    }
  }, []);

  return null;
}
