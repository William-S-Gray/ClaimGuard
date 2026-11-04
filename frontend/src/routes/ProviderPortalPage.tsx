import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ArrowDown, ArrowRight, ArrowUp, Download, LogOut, ShieldCheck } from 'lucide-react';
import {
  downloadPortalReport,
  fetchPortalAppeals,
  fetchPortalClaims,
  fetchPortalDashboard,
  submitPortalAppeal,
  type PortalClaim,
} from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { TrustBadgeChip } from '@/components/shared/Badges';
import { ErrorState } from '@/components/shared/EmptyState';
import { formatCurrency, formatDate } from '@/lib/formatters';

// The portal has no live socket (that feed is staff-only), so it polls instead.
const REFRESH_MS = 60_000;

const STATUS_STYLE: Record<PortalClaim['status'], string> = {
  APPROVED: 'bg-green-50 text-green-700',
  UNDER_REVIEW: 'bg-amber-50 text-amber-700',
  DISPUTED: 'bg-red-50 text-red-700',
  REJECTED: 'bg-gray-100 text-gray-700',
};
const STATUS_LABEL: Record<PortalClaim['status'], string> = {
  APPROVED: 'Approved',
  UNDER_REVIEW: 'Under review',
  DISPUTED: 'Disputed',
  REJECTED: 'Rejected',
};

function pct(value: number | null, empty: string) {
  return value === null ? empty : `${value.toFixed(1)}%`;
}

function Change({ value }: { value: number | null }) {
  if (value === null) return <span className="text-xs text-gray-400">No score a month ago yet</span>;
  const Icon = value > 0 ? ArrowUp : value < 0 ? ArrowDown : ArrowRight;
  const color = value > 0 ? 'text-green-600' : value < 0 ? 'text-red-600' : 'text-gray-500';
  return (
    <span className={`inline-flex items-center gap-1 text-sm font-medium ${color}`}>
      <Icon size={14} aria-hidden />
      {value > 0 ? '+' : ''}
      {value} since last month
    </span>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-xl font-semibold text-gray-900">{value}</p>
      {hint && <p className="text-[11px] text-gray-400">{hint}</p>}
    </div>
  );
}

function AppealsCard() {
  const qc = useQueryClient();
  const [reason, setReason] = useState('');
  const { data: appeals = [] } = useQuery({ queryKey: ['portal-appeals'], queryFn: fetchPortalAppeals });
  const submit = useMutation({
    mutationFn: () => submitPortalAppeal(reason.trim()),
    onSuccess: () => {
      setReason('');
      qc.invalidateQueries({ queryKey: ['portal-appeals'] });
    },
  });
  const hasOpen = appeals.some((a) => a.status === 'OPEN');

  return (
    <section className="page-card p-5" aria-labelledby="appeals-h">
      <h2 id="appeals-h" className="font-semibold text-gray-900 mb-1">Appeal your score</h2>
      <p className="text-xs text-gray-500 mb-3">
        Think your TrustScore is wrong? Tell us why and the ClaimGuard team will review it.
      </p>
      {hasOpen ? (
        <p className="text-sm text-amber-700 bg-amber-50 rounded p-3 mb-4">
          You have an appeal under review. You can submit another once it is resolved.
        </p>
      ) : (
        <form
          className="mb-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit.mutate();
          }}
        >
          <label htmlFor="appeal-reason" className="block text-xs font-medium text-gray-600 mb-1">
            Reason (at least 10 characters)
          </label>
          <textarea
            id="appeal-reason"
            className="form-input min-h-[80px] resize-y"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={2000}
          />
          {submit.isError && (
            <p className="text-xs text-red-600 mt-1" role="alert">{(submit.error as Error).message}</p>
          )}
          <button
            type="submit"
            className="btn-primary mt-2"
            disabled={reason.trim().length < 10 || submit.isPending}
          >
            {submit.isPending ? 'Submitting…' : 'Submit appeal'}
          </button>
        </form>
      )}
      <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">History</h3>
      {appeals.length === 0 ? (
        <p className="text-sm text-gray-400">No appeals yet.</p>
      ) : (
        <ul className="space-y-2">
          {appeals.map((a) => (
            <li key={a.id} className="border border-gray-100 rounded p-3 text-sm">
              <div className="flex justify-between gap-2">
                <span className="font-medium">{a.status === 'OPEN' ? 'Under review' : a.status === 'UPHELD' ? 'Upheld' : 'Not upheld'}</span>
                <span className="text-xs text-gray-400">{formatDate(a.submittedAt)} · score {a.scoreAtSubmission}</span>
              </div>
              <p className="text-gray-600 mt-1">{a.reason}</p>
              {a.outcomeNotes && <p className="text-gray-800 mt-1"><span className="text-gray-500">Outcome:</span> {a.outcomeNotes}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ProviderPortalPage() {
  const { user, logout } = useAuthStore();
  const dash = useQuery({ queryKey: ['portal-dashboard'], queryFn: fetchPortalDashboard, refetchInterval: REFRESH_MS });
  const claims = useQuery({ queryKey: ['portal-claims'], queryFn: () => fetchPortalClaims(30), refetchInterval: REFRESH_MS });
  const [downloading, setDownloading] = useState(false);

  const d = dash.data;
  const trend = (d?.history ?? []).map((h) => ({ date: formatDate(h.createdAt, 'dd MMM'), score: h.score }));

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <ShieldCheck className="text-brand-navy shrink-0" size={22} aria-hidden />
            <div className="min-w-0">
              <p className="font-semibold text-gray-900 truncate">{d?.provider.name ?? 'Provider portal'}</p>
              <p className="text-xs text-gray-500 truncate">
                {d ? `${d.provider.type} · ${d.provider.city} · ${d.provider.code}` : user?.email}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1"
              disabled={downloading}
              onClick={async () => {
                setDownloading(true);
                try {
                  await downloadPortalReport(90);
                } finally {
                  setDownloading(false);
                }
              }}
            >
              <Download size={14} aria-hidden /> <span className="hidden sm:inline">90-day report</span>
            </button>
            <button type="button" className="icon-btn" onClick={() => logout()} aria-label="Sign out">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-6 space-y-5">
        {dash.isError ? (
          <ErrorState />
        ) : !d ? (
          <div className="skeleton h-40 rounded-xl" />
        ) : (
          <>
            <div className="grid gap-5 md:grid-cols-3">
              <section className="page-card p-5" aria-labelledby="score-h">
                <h2 id="score-h" className="text-xs font-semibold text-gray-500 uppercase">Your TrustScore</h2>
                <div className="flex items-end gap-3 mt-2">
                  <span className="text-5xl font-bold text-gray-900">{d.trustScore}</span>
                  <span className="text-gray-400 mb-1">/ 100</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <TrustBadgeChip badge={d.badge} />
                  <Change value={d.changeFromLastMonth} />
                </div>
                <p className="text-[11px] text-gray-400 mt-3">
                  Based on {d.breakdown.totalClaims} claims · confidence {Math.round(d.breakdown.confidence * 100)}%
                </p>
              </section>

              <section className="page-card p-5 md:col-span-2" aria-labelledby="breakdown-h">
                <h2 id="breakdown-h" className="text-xs font-semibold text-gray-500 uppercase mb-3">What drives your score</h2>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                  <Stat label="Claim accuracy" value={pct(d.breakdown.claimAccuracy, '—')} hint="Claims members did not dispute" />
                  <Stat
                    label="Investigation success"
                    value={pct(d.breakdown.investigationSuccess, 'No cases')}
                    hint={`${d.breakdown.investigationsClosed} closed cases found legitimate`}
                  />
                  <Stat label="Member satisfaction" value={pct(d.breakdown.memberSatisfaction, 'Not rated')} hint="Ratings aren't collected yet" />
                  <Stat label="Shortfall index" value={d.breakdown.shortfallIndex.toFixed(2)} hint="1.00 = members pay what's expected" />
                  <Stat label="Flagged claims (90 days)" value={String(d.breakdown.flags90d)} />
                  <Stat
                    label={`Peers in ${d.peers.region}`}
                    value={d.peers.percentile === null ? '—' : `Above ${d.peers.percentile}% of peers`}
                    hint={d.peers.average === null ? 'No peers yet' : `Average ${d.peers.average} vs your ${d.trustScore}`}
                  />
                </div>
              </section>
            </div>

            <section className="page-card p-5" aria-labelledby="trend-h">
              <h2 id="trend-h" className="font-semibold text-gray-900 mb-3">Score trend</h2>
              {trend.length < 2 ? (
                <p className="text-sm text-gray-400">The trend appears once your score has been recalculated a few times.</p>
              ) : (
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={trend} margin={{ top: 5, right: 10, bottom: 0, left: -20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                      <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Line type="monotone" dataKey="score" stroke="#1A4D8F" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>
          </>
        )}

        <div className="grid gap-5 lg:grid-cols-3">
          <section className="page-card lg:col-span-2 overflow-hidden" aria-labelledby="claims-h">
            <h2 id="claims-h" className="font-semibold text-gray-900 p-5 pb-3">Claims in the last 30 days</h2>
            {claims.isError ? (
              <ErrorState />
            ) : (claims.data ?? []).length === 0 ? (
              <p className="text-sm text-gray-400 px-5 pb-5">No claims in the last 30 days.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table w-full">
                  <thead>
                    <tr>
                      <th>Claim</th>
                      <th>Date</th>
                      <th>Service</th>
                      <th>Member</th>
                      <th className="text-right">Amount</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(claims.data ?? []).map((c) => (
                      <tr key={c.claimRef}>
                        <td className="font-mono text-xs">{c.claimRef}</td>
                        <td className="text-xs">{formatDate(c.submittedAt)}</td>
                        <td className="text-xs">{c.service ?? '—'}</td>
                        <td className="text-xs">{c.member}</td>
                        <td className="text-right text-xs">{formatCurrency(c.amount)}</td>
                        <td>
                          <span className={`badge ${STATUS_STYLE[c.status]}`}>{STATUS_LABEL[c.status]}</span>
                          {c.disputeReason && <p className="text-[11px] text-red-600 mt-1">{c.disputeReason}</p>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <AppealsCard />
        </div>
      </main>
    </div>
  );
}
