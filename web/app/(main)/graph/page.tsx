import type { Metadata } from "next";
import ConceptGraph from "@/components/ConceptGraph";

export const metadata: Metadata = { title: "개념 그래프 — KB" };

export default function GraphPage() {
  return (
    <div>
      <h1 className="text-3xl font-bold text-gray-900 mb-2">개념 그래프</h1>
      <p className="text-sm text-gray-500 mb-4">
        개념 간 [[위키링크]] 연결 관계를 시각화합니다. 노드를 클릭하면 해당 개념 페이지로 이동합니다.
      </p>
      <ConceptGraph />
    </div>
  );
}
