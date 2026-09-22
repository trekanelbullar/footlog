import type { Session } from "@/lib/auth";
import { w10ListSources, w18ListReports, w19GetReport } from "@/lib/worker";
import { newEventNos } from "@/lib/article";
import ReportViewer from "@/components/ReportViewer";
import VersionSwitcher from "@/components/VersionSwitcher";
import AddSourceButton from "@/components/AddSourceButton";
import { EmptyState } from "@/components/ui/Feedback";
import type { TimelineItem } from "@/lib/worker-types";

/**
 * レポートタブ（design/ui-guidelines.md a）：上に版の切り替えと検索を横一列、
 * その下に本文（記事の体裁）と右の根拠パネル。版が無いときは空の状態。
 */
export default async function ReportTab({
  session,
  pid,
  projectName,
  versionNo,
  hasSources,
  running = false,
}: {
  session: Session;
  pid: string;
  projectName: string;
  versionNo: number | null;
  hasSources: boolean;
  running?: boolean;
}) {
  const reports = await w18ListReports(session, pid);

  if (reports.length === 0) {
    return hasSources ? (
      <EmptyState
        title="まだレポートがありません"
        body="右上の「今すぐ確認」を押すと、登録した資料から決定・理由・却下した案を拾ってレポートを作ります。"
      />
    ) : (
      <EmptyState
        title="まず資料を追加してください"
        body="AIとの会話ログやファイルを登録すると、そこから決定・理由・却下した案を拾ってレポートにします。"
        action={<AddSourceButton pid={pid} variant="primary" />}
      />
    );
  }

  const target = versionNo ?? Math.max(...reports.map((r) => r.version_no));
  const [report, sources] = await Promise.all([
    w19GetReport(session, pid, target),
    w10ListSources(session, pid),
  ]);

  // NEW の印：1つ前の見られる版の時系列に無い出来事。前の版が無ければ付けない。
  const previous = reports
    .filter((r) => r.version_no < target && !r.withheld)
    .sort((a, b) => b.version_no - a.version_no)[0];
  let previousTimeline: TimelineItem[] | null = null;
  if (previous && !report.withheld) {
    try {
      previousTimeline = (await w19GetReport(session, pid, previous.version_no))
        .timeline;
    } catch {
      previousTimeline = null; // 前の版が読めなくても、NEW なしで出す
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <VersionSwitcher pid={pid} reports={reports} currentVersion={target} />
      </div>
      {/* 版が変わったら内部状態を確実にリセットするため key で再マウントする */}
      <ReportViewer
        key={report.version_no}
        pid={pid}
        report={report}
        sources={sources}
        projectName={projectName}
        newEventNos={[...newEventNos(report.timeline, previousTimeline)]}
        running={running}
      />
    </div>
  );
}
