import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link, useNavigate } from '@tanstack/react-router';
import { fetchClaimByRef, approveClaim, rejectClaim, openInvestigation, requestClaimInfo } from '@/lib/api';
import { AppShell } from '@/components/layout/AppShell';
import { RiskScoreRing } from '@/components/shared/RiskScoreRing';
import { StatusBadge, PriorityBadge, ChannelBadge } from '@/components/shared/Badges';
import { FlagBadgeList } from '@/components/shared/FlagBadge';
import { LatencyBadge } from '@/components/shared/LatencyBadge';
import { TrustBadgeChip } from '@/components/shared/Badges';
import { ErrorState, EmptyState } from '@/components/shared/EmptyState';
import { Skeleton } from '@/components/shared/SkeletonLoader';
import { useAuthStore } from '@/stores/authStore';
import { ApiError } from '@/lib/apiClient';
import { formatCurrency, formatDate, formatDateTime, formatTimeAgo } from '@/lib/formatters';
import { ArrowLeft, CheckCircle, XCircle, AlertTriangle, Clock, Loader2, FolderSearch, MessageSquareMore } from 'lucide-react';

export function ClaimDetailPage() {
  const { claimRef } = useParams({ from: '/queue/$claimRef' });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const canApprove = !!user && (user.isSuperuser || user.permissions.includes('claim:approve'));
  const canReject = !!user && (user.isSuperuser || user.permissions.includes('claim:reject'));
  const canInvestigate =
    !!user && (user.isSuperuser || user.permissions.includes('investigation:manage'));

  const { data: claim, isLoading, isError, refetch } = useQuery({
    queryKey: ['claim', claimRef],
    queryFn: () => fetchClaimByRef(claimRef),
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['claim', claimRef] });
    queryClient.invalidateQueries({ queryKey: ['queue'] });
    queryClient.invalidateQueries({ queryKey: ['live-feed'] });
    queryClient.invalidateQueries({ queryKey: ['dashboard-metrics'] });
  };

  const approveMutation = useMutation({
    mutationFn: () => approveClaim(claimRef),
    onSuccess: invalidateAll,
  });
  const rejectMutation = useMutation({
    mutationFn: (reason: 'REJECT_FRAUD' | 'REJECT_ERROR') => rejectClaim(claimRef, reason),
    onSuccess: invalidateAll,
  });
  const investigateMutation = useMutation({
    mutationFn: () => openInvestigation(claimRef),
    onSuccess: (inv) => {
      queryClient.invalidateQueries({ queryKey: ['investigations'] });
      navigate({
        to: '/investigations/$investigationId',
        params: { investigationId: inv.id },
      });
    },
  });

  const actionPending = approveMutation.isPending || rejectMutation.isPending;
  const actionError =
    (approveMutation.error as ApiError | null)?.message ??
    (rejectMutation.error as ApiError | null)?.message ??
    null;

  if (isLoading) {
    return (
      <AppShell title="Claim Detail" subtitle={claimRef}>
        <div className="space-y-4">
          <Skeleton className="h-48 w-full rounded-xl" />
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      </AppShell>
    );
  }

  if (isError) {
    return (
      <AppShell title="Claim Detail">
        <ErrorState onRetry={refetch} />
      </AppShell>
    );
  }

  if (!claim) {
    return (
      <AppShell title="Claim Not Found">
        <EmptyState title={`Claim ${claimRef} not found`} description="This claim may have been archived or the reference is incorrect." />
      </AppShell>
    );
  }

  const shortfallDeviation = ((claim.memberShortfall - claim.expectedShortfall[1]) / claim.expectedShortfall[1] * 100);

  return (
    <AppShell title={`Claim ${claim.claimRef}`} subtitle={`NH263: ${claim.nh263Ref} · ${claim.member.name}`}>
      <div className="max-w-screen-xl mx-auto">
        {/* Back */}
        <Link to="/queue" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-brand-navy mb-4 transition-colors">
          <ArrowLeft size={14} /> Back to Queue
        </Link>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
          {/* ── Left Column ─────────────────────────────────────────────── */}
          <div className="xl:col-span-2 space-y-4">
            {/* Header Card */}
            <div className="page-card p-5">
              <div className="flex items-start gap-5">
                <RiskScoreRing score={claim.riskScore} size={96} strokeWidth={10} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-2">
                    <span className="text-2xl font-bold text-gray-900">{claim.claimRef}</span>
                    <PriorityBadge priority={claim.priority} />
                    <LatencyBadge ms={claim.latencyMs} />
                  </div>
                  <div className="flex items-center gap-3 flex-wrap mb-3">
                    <StatusBadge decision={claim.decision} />
                    {claim.memberNotificationChannel && (
                      <ChannelBadge channel={claim.memberNotificationChannel} />
                    )}
                    <MemberResponseBadge status={claim.memberResponse} />
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 text-xs">
                    <div><span className="text-gray-400">NH263 Ref</span><p className="font-medium text-gray-700">{claim.nh263Ref}</p></div>
                    <div><span className="text-gray-400">Service Date</span><p className="font-medium text-gray-700">{formatDate(claim.serviceDate)}</p></div>
                    <div><span className="text-gray-400">Submitted</span><p className="font-medium text-gray-700">{formatTimeAgo(claim.submittedAt)}</p></div>
                    <div><span className="text-gray-400">SLA Deadline</span><p className="font-medium text-gray-700">{formatDate(claim.slaDeadline)}</p></div>
                  </div>
                </div>
              </div>
            </div>

            {/* AI Explanation */}
            <div className="page-card p-5">
              <h3 className="mb-3">AI Risk Explanation</h3>
              <div className="bg-red-50 border border-red-100 rounded-xl p-4 mb-4">
                <p className="text-sm text-gray-700 leading-relaxed">{claim.aiExplanation}</p>
              </div>
              <h3 className="mb-3">SHAP Feature Contributions</h3>
              <div className="space-y-2.5">
                {claim.shapContributions.map((shap) => {
                  const abs = Math.abs(shap.contribution);
                  const pct = Math.round(abs * 100);
                  return (
                    <div key={shap.feature}>
                      <div className="flex justify-between text-xs mb-1">
                        <span className="text-gray-600">{shap.feature}</span>
                        <span className={shap.direction === 'positive' ? 'text-red-600 font-semibold' : 'text-green-600 font-semibold'}>
                          {shap.direction === 'positive' ? '+' : '-'}{pct}%
                        </span>
                      </div>
                      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${shap.direction === 'positive' ? 'bg-red-400' : 'bg-green-400'}`}
                          style={{ width: `${pct}%` }}
                          role="progressbar"
                          aria-valuenow={pct}
                          aria-valuemin={0}
                          aria-valuemax={100}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Flags */}
            <div className="page-card p-5">
              <h3 className="mb-3">Risk Flags</h3>
              <FlagBadgeList flags={claim.flags} />
            </div>

            {/* Claim Items */}
            <div className="page-card">
              <div className="px-5 py-3 border-b border-gray-100">
                <h3>Claim Line Items</h3>
              </div>
              <table className="data-table" aria-label="Claim line items">
                <thead>
                  <tr>
                    <th>Description</th>
                    <th>NAPPI / ICD-10</th>
                    <th>Qty</th>
                    <th>Unit Price</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {claim.items.map((item) => (
                    <tr key={item.id} className="hover:bg-transparent cursor-default">
                      <td className="font-medium text-gray-800">{item.description}</td>
                      <td className="text-gray-400 font-mono text-xs">{item.nappiCode ?? item.icd10Code ?? '—'}</td>
                      <td>{item.quantity}</td>
                      <td>{formatCurrency(item.unitPrice)}</td>
                      <td className="font-semibold">{formatCurrency(item.total)}</td>
                    </tr>
                  ))}
                  <tr className="bg-gray-50 hover:bg-gray-50 cursor-default font-bold">
                    <td colSpan={4} className="text-right text-sm">Total Claimed</td>
                    <td className="text-brand-navy">{formatCurrency(claim.claimedAmount)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Member + Provider Info */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Member */}
              <div className="page-card p-5">
                <h3 className="mb-3">Member</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-gray-500">Name</span><span className="font-medium">{claim.member.name}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Number</span><span className="font-mono text-xs">{claim.member.memberNumber}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Plan</span><span className="font-semibold text-brand-navy">{claim.member.plan}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">City</span><span>{claim.member.city}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Conditions</span><span>{claim.member.conditions.length > 0 ? claim.member.conditions.join(', ') : <span className="text-gray-400">None registered</span>}</span></div>
                </div>
              </div>

              {/* Provider */}
              <div className="page-card p-5">
                <h3 className="mb-3">Provider</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-gray-500">Name</span><span className="font-medium">{claim.provider.name}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Code</span><span className="font-mono text-xs">{claim.provider.code}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Type</span><span>{claim.provider.type}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">TrustScore</span><TrustBadgeChip badge={claim.provider.badge} /></div>
                  <div className="flex justify-between"><span className="text-gray-500">Dispute Rate</span><span className={claim.provider.disputeRate > 5 ? 'text-red-600 font-semibold' : 'text-green-600'}>{claim.provider.disputeRate}%</span></div>
                </div>
              </div>
            </div>

            {/* Shortfall Comparison */}
            <div className="page-card p-5">
              <h3 className="mb-4">Shortfall Analysis</h3>
              <div className="grid grid-cols-3 gap-4">
                <div className="text-center p-3 bg-gray-50 rounded-xl">
                  <p className="text-xs text-gray-500 mb-1">Member Shortfall</p>
                  <p className="text-2xl font-bold text-gray-900">{formatCurrency(claim.memberShortfall)}</p>
                </div>
                <div className="text-center p-3 bg-green-50 rounded-xl">
                  <p className="text-xs text-gray-500 mb-1">Expected Range</p>
                  <p className="text-2xl font-bold text-green-700">{formatCurrency(claim.expectedShortfall[0])} – {formatCurrency(claim.expectedShortfall[1])}</p>
                </div>
                <div className={`text-center p-3 rounded-xl ${shortfallDeviation > 30 ? 'bg-red-50' : 'bg-amber-50'}`}>
                  <p className="text-xs text-gray-500 mb-1">Deviation</p>
                  <p className={`text-2xl font-bold ${shortfallDeviation > 30 ? 'text-red-600' : 'text-amber-600'}`}>
                    {shortfallDeviation > 0 ? '+' : ''}{shortfallDeviation.toFixed(0)}%
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* ── Right Column ─────────────────────────────────────────────── */}
          <div className="space-y-4">
            {/* Agent Actions */}
            <div className="page-card p-5">
              <h3 className="mb-4">Agent Actions</h3>

              {!canApprove && !canReject ? (
                <p className="text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg p-3">
                  Your role has read-only access. Claim decisions require an agent or admin role.
                </p>
              ) : (
                <div className="space-y-2">
                  <button
                    className="btn-primary w-full justify-center"
                    aria-label="Approve claim"
                    disabled={!canApprove || actionPending}
                    onClick={() => approveMutation.mutate()}
                  >
                    {approveMutation.isPending ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : (
                      <CheckCircle size={15} />
                    )}
                    Approve Claim
                  </button>
                  <button
                    className="btn-danger w-full justify-center"
                    aria-label="Reject as fraud"
                    disabled={!canReject || actionPending}
                    onClick={() => rejectMutation.mutate('REJECT_FRAUD')}
                  >
                    {rejectMutation.isPending && rejectMutation.variables === 'REJECT_FRAUD' ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : (
                      <XCircle size={15} />
                    )}
                    Reject — Fraud
                  </button>
                  <button
                    className="btn-secondary w-full justify-center"
                    aria-label="Reject as error"
                    disabled={!canReject || actionPending}
                    onClick={() => rejectMutation.mutate('REJECT_ERROR')}
                  >
                    {rejectMutation.isPending && rejectMutation.variables === 'REJECT_ERROR' ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : (
                      <AlertTriangle size={15} />
                    )}
                    Reject — Error
                  </button>
                </div>
              )}

              {canApprove && <RequestInfo claimRef={claim.claimRef} decision={claim.decision} onDone={invalidateAll} />}

              {canInvestigate && (
                <button
                  className="btn-secondary w-full justify-center mt-2 text-brand-navy border-brand-navy/20 bg-brand-navy/5 hover:bg-brand-navy/10"
                  aria-label="Open investigation"
                  disabled={investigateMutation.isPending}
                  onClick={() => investigateMutation.mutate()}
                >
                  {investigateMutation.isPending ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <FolderSearch size={15} />
                  )}
                  Open Investigation
                </button>
              )}

              {actionError && (
                <p className="mt-3 text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg p-2" role="alert">
                  {actionError}
                </p>
              )}
              {(approveMutation.isSuccess || rejectMutation.isSuccess) && !actionError && (
                <p className="mt-3 text-xs text-green-700 bg-green-50 border border-green-100 rounded-lg p-2">
                  Decision recorded. The claim status has been updated.
                </p>
              )}

              <div className="mt-4 p-3 bg-amber-50 border border-amber-100 rounded-xl">
                <div className="flex items-center gap-2 text-xs text-amber-700">
                  <Clock size={12} />
                  <span>SLA: {formatDateTime(claim.slaDeadline)}</span>
                </div>
              </div>
            </div>

            {/* WhatsApp Message */}
            {claim.memberNotificationSent && (
              <div className="page-card p-5">
                <h3 className="mb-3">MemberGuard Notification</h3>
                <div className="bg-[#E7FFDB] rounded-2xl rounded-tl-none p-3 text-sm text-gray-800 leading-relaxed font-light shadow-sm">
                  <p className="text-[10px] text-gray-500 font-semibold mb-2">Cimas ClaimGuard · Delivered</p>
                  Hi <strong>{claim.member.name.split(' ')[0]}</strong>! {claim.provider.name} submitted a claim for {claim.items.length} item{claim.items.length !== 1 ? 's' : ''} today ({formatCurrency(claim.claimedAmount)}).
                  <br /><br />
                  Your shortfall was {formatCurrency(claim.memberShortfall)} — slightly above the usual {formatCurrency(claim.expectedShortfall[0])}–{formatCurrency(claim.expectedShortfall[1])} for this visit type.
                  <br /><br />
                  Did you visit today?<br />
                  Reply <strong>1</strong> to confirm ✓<br />
                  Reply <strong>2</strong> if something is wrong ✗<br />
                  Reply <strong>HELP</strong> for Cimas support.
                  <br /><br />
                  <span className="text-xs text-gray-400">Cimas is protecting your benefits. Ref: {claim.claimRef}.</span>
                </div>
                {claim.memberResponse !== 'PENDING' && (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <MemberResponseBadge status={claim.memberResponse} />
                    {claim.memberRating != null && (
                      <span className="badge text-xs bg-amber-50 text-amber-700 border-amber-200">
                        Care rated {claim.memberRating}/5
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Timeline */}
            <div className="page-card p-5">
              <h3 className="mb-4">Claim Timeline</h3>
              <div className="relative space-y-0">
                {claim.timeline.map((event, idx) => (
                  <div key={event.id} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 mt-0.5 ${
                        event.type === 'system' ? 'bg-blue-400' :
                        event.type === 'member' ? 'bg-green-500' :
                        event.type === 'agent' ? 'bg-purple-400' : 'bg-gray-400'
                      }`} />
                      {idx < claim.timeline.length - 1 && (
                        <div className="w-px flex-1 bg-gray-100 my-1 min-h-4" />
                      )}
                    </div>
                    <div className="pb-4 min-w-0">
                      <p className="text-xs font-semibold text-gray-700">{event.event}</p>
                      <p className="text-[10px] text-gray-500 mt-0.5">{event.description}</p>
                      <p className="text-[9px] text-gray-400 mt-0.5">{formatDateTime(event.timestamp)} · {event.actor}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

const DECIDED = ['APPROVE', 'REJECT_FRAUD', 'REJECT_ERROR'];

function RequestInfo({ claimRef, decision, onDone }: { claimRef: string; decision: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState('');
  const ask = useMutation({
    mutationFn: () => requestClaimInfo(claimRef, message.trim()),
    onSuccess: () => {
      setOpen(false);
      setMessage('');
      onDone();
    },
  });

  if (decision === 'INFO_NEEDED') {
    return (
      <p className="mt-2 text-xs text-sky-800 bg-sky-50 border border-sky-100 rounded-lg p-2">
        Waiting on the provider. Their answer appears in the timeline and the claim returns to review.
      </p>
    );
  }
  if (DECIDED.includes(decision)) return null;
  if (!open) {
    return (
      <button type="button" className="btn-secondary w-full justify-center mt-2" onClick={() => setOpen(true)}>
        <MessageSquareMore size={15} /> Request info from provider
      </button>
    );
  }
  return (
    <div className="mt-2 space-y-2">
      <label htmlFor="info-message" className="block text-xs font-medium text-gray-600">
        What do you need? (the provider sees this)
      </label>
      <textarea
        id="info-message"
        className="form-input min-h-[64px] text-sm"
        value={message}
        maxLength={1000}
        onChange={(e) => setMessage(e.target.value)}
      />
      {ask.isError && (
        <p className="text-xs text-red-600" role="alert">
          {(ask.error as ApiError).message ?? 'Could not send the request.'}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-primary flex-1 justify-center"
          disabled={message.trim().length < 5 || ask.isPending}
          onClick={() => ask.mutate()}
        >
          {ask.isPending && <Loader2 size={15} className="animate-spin" />} Send request
        </button>
        <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function MemberResponseBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    PENDING: 'bg-gray-50 text-gray-600 border-gray-200',
    CONFIRMED: 'bg-green-50 text-green-700 border-green-200',
    DISPUTED: 'bg-red-50 text-red-700 border-red-200',
    NO_RESPONSE: 'bg-gray-100 text-gray-500 border-gray-200',
  };
  const labels: Record<string, string> = {
    PENDING: 'Awaiting response',
    CONFIRMED: '✓ Confirmed by member',
    DISPUTED: '✗ Disputed by member',
    NO_RESPONSE: 'No response',
  };
  return (
    <span className={`badge text-xs ${styles[status] ?? 'bg-gray-100 text-gray-500'}`}>
      {labels[status] ?? status}
    </span>
  );
}
