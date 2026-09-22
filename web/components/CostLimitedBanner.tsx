/**
 * AD-9：W3 の `cost_limited_today` が true のときに、プロジェクトの画面と
 * レポートの画面の上部に出す帯。
 */
export default function CostLimitedBanner() {
  return (
    <div className="rounded bg-amber-50 p-3 text-sm text-amber-800">
      本日は費用の上限に達したため、分析を行っていません
    </div>
  );
}
