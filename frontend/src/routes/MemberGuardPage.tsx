import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchQueue, sendMemberAlert, recordMemberResponse,
  type MemberAlert,
} from '@/lib/api';
import { ApiError } from '@/lib/apiClient';
import { AppShell } from '@/components/layout/AppShell';
import { StatCard } from '@/components/shared/StatCard';
import { EmptyState } from '@/components/shared/EmptyState';
import type { Claim, ClaimSummary, RiskLevel } from '@/types';
import { cn } from '@/lib/utils';
import { formatCurrency } from '@/lib/formatters';
import {
  MessageSquare, Smartphone, Hash, Send, ShieldCheck, ShieldAlert,
  Check, X, BellRing, Phone, RefreshCw,
} from 'lucide-react';

type ChannelKey = 'WHATSAPP' | 'SMS' | 'USSD';

const CHANNELS: { key: ChannelKey; label: string; icon: typeof MessageSquare }[] = [
  { key: 'WHATSAPP', label: 'WhatsApp', icon: MessageSquare },
  { key: 'SMS', label: 'SMS', icon: Smartphone },
  { key: 'USSD', label: 'USSD', icon: Hash },
];

const RISK_CLASS: Record<RiskLevel, string> = {
  CRITICAL: 'bg-red-50 text-red-700 border-red-200',
  HIGH: 'bg-orange-50 text-orange-700 border-orange-200',
  MEDIUM: 'bg-amber-50 text-amber-700 border-amber-200',
  LOW: 'bg-green-50 text-green-700 border-green-200',
};

function RiskPill({ level }: { level: RiskLevel }) {
  return <span className={cn('badge', RISK_CLASS[level])}>{level}</span>;
}

function initials(name: string) {
  return name.split(' ').map((n) => n[0]).slice(0, 2).join('');
}

/** The phone screen: renders the sent alert per channel, then the confirm prompt. */
function PhoneFrame({
  claim, alert, response, onRespond, responding,
}: {
  claim: ClaimSummary | null;
  alert: MemberAlert | null;
  response: 'CONFIRMED' | 'DISPUTED' | null;
  onRespond: (r: 'CONFIRMED' | 'DISPUTED') => void;
  responding: boolean;
}) {
  return (
    <div className="mx-auto w-[300px] rounded-[2.2rem] border-[10px] border-gray-900 bg-gray-900 shadow-xl">
      {/* Notch */}
      <div className="relative h-6 rounded-t-[1.3rem] bg-gray-900">
        <div className="absolute left-1/2 top-1.5 h-1.5 w-16 -translate-x-1/2 rounded-full bg-gray-700" />
      </div>

      <div className="min-h-[460px] rounded-b-[1.3rem] bg-gray-50 p-3 flex flex-col">
        {!alert || !claim ? (
          <div className="flex-1 flex flex-col items-center justify-center text-center text-gray-400 gap-2">
            <BellRing size={28} />
            <p className="text-xs px-6">Send an alert to preview the member's phone.</p>
          </div>
        ) : (
          <>
            {/* Chat / message header */}
            <div className="flex items-center gap-2 pb-2 border-b border-gray-200">
              <span className="w-7 h-7 rounded-full bg-brand-navy text-white flex items-center justify-center text-[10px] font-bold">
                CG
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-semibold text-gray-800 leading-tight">Cimas ClaimGuard</p>
                <p className="text-[9px] text-gray-400 leading-tight">
                  {alert.channel === 'USSD' ? alert.ussdCode : alert.to}
                </p>
              </div>
              <span className="ml-auto text-[8px] uppercase tracking-wide text-gray-400">
                {CHANNELS.find((c) => c.key === alert.channel)?.label}
              </span>
            </div>

            {/* The alert message bubble (USSD gets a terminal look) */}
            <div className="py-3 flex-1 space-y-3 overflow-y-auto">
              {alert.channel === 'USSD' ? (
                <div className="rounded-lg bg-gray-900 text-green-300 font-mono text-[10px] leading-relaxed p-3 whitespace-pre-wrap">
                  {alert.message}
                </div>
              ) : (
                <div className={cn(
                  'max-w-[85%] rounded-2xl rounded-tl-sm px-3 py-2 text-[11px] leading-snug text-gray-800',
                  alert.channel === 'WHATSAPP' ? 'bg-green-100' : 'bg-white border border-gray-200'
                )}>
                  {alert.message}
                </div>
              )}

              {/* Member's reply shown once they respond */}
              {response && (
                <div className="max-w-[80%] ml-auto rounded-2xl rounded-tr-sm bg-brand-navy px-3 py-2 text-[11px] font-medium text-white">
                  {response === 'CONFIRMED' ? 'YES — I received this service.' : 'NO — I did not receive this.'}
                </div>
              )}
            </div>

            {/* Confirmation prompt / result */}
            {!response ? (
              <div className="border-t border-gray-200 pt-3">
                <p className="text-center text-[11px] font-semibold text-gray-700 mb-2">
                  Did you receive this service?
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    className="flex items-center justify-center gap-1 rounded-lg bg-brand-navy py-2 text-[11px] font-semibold text-white disabled:opacity-50"
                    onClick={() => onRespond('CONFIRMED')} disabled={responding}
                  >
                    <Check size={13} /> Yes, I did
                  </button>
                  <button
                    className="flex items-center justify-center gap-1 rounded-lg border border-gray-300 bg-white py-2 text-[11px] font-semibold text-gray-700 disabled:opacity-50"
                    onClick={() => onRespond('DISPUTED')} disabled={responding}
                  >
                    <X size={13} /> No, I didn't
                  </button>
                </div>
                <p className="text-center text-[9px] text-gray-400 mt-2">Your reply protects your benefits.</p>
              </div>
            ) : (
              <div className={cn(
                'border-t pt-3 text-center',
                response === 'CONFIRMED' ? 'border-green-200' : 'border-red-200'
              )}>
                {response === 'CONFIRMED' ? (
                  <p className="flex items-center justify-center gap-1.5 text-[11px] font-semibold text-green-700">
                    <ShieldCheck size={14} /> Thank you — claim verified.
                  </p>
                ) : (
                  <p className="flex items-center justify-center gap-1.5 text-[11px] font-semibold text-red-700">
                    <ShieldAlert size={14} /> Reported. Our team will investigate.
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function MemberGuardPage() {
  const queryClient = useQueryClient();
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [channel, setChannel] = useState<ChannelKey>('WHATSAPP');
  const [alert, setAlert] = useState<MemberAlert | null>(null);
  const [claim, setClaim] = useState<Claim | null>(null);
  const [response, setResponse] = useState<'CONFIRMED' | 'DISPUTED' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: claims = [], isLoading } = useQuery({
    queryKey: ['queue'],
    queryFn: () => fetchQueue(),
  });

  // Default the picker to the first claim still awaiting the member's reply.
  const selected = useMemo(() => {
    if (selectedRef) return claims.find((c) => c.claimRef === selectedRef) ?? null;
    return claims.find((c) => c.memberResponse === 'PENDING') ?? claims[0] ?? null;
  }, [claims, selectedRef]);

  const pendingCount = claims.filter((c) => c.memberResponse === 'PENDING').length;
  const disputedCount = claims.filter((c) => c.memberResponse === 'DISPUTED').length;
  const confirmedCount = claims.filter((c) => c.memberResponse === 'CONFIRMED').length;

  const errorFrom = (e: unknown) =>
    e instanceof ApiError ? e.message : 'Something went wrong. Please try again.';

  const alertMutation = useMutation({
    mutationFn: () => sendMemberAlert(selected!.claimRef, channel),
    onSuccess: (res) => {
      setAlert(res.alert);
      setClaim(res.claim);
      setResponse(null);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
    onError: (e) => setError(errorFrom(e)),
  });

  const responseMutation = useMutation({
    mutationFn: (r: 'CONFIRMED' | 'DISPUTED') =>
      recordMemberResponse((claim ?? selected)!.claimRef, r),
    onSuccess: (updated, r) => {
      setClaim(updated);
      setResponse(r);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (e) => setError(errorFrom(e)),
  });

  const activeClaim = claim ?? selected;

  return (
    <AppShell title="MemberGuard" subtitle="Instant member alerts & claim verification">
      <div className="max-w-screen-xl mx-auto space-y-5">
        {/* Summary */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard title="Awaiting Reply" value={pendingCount} icon={<BellRing size={20} />} accentColor="#D97706" />
          <StatCard title="Confirmed" value={confirmedCount} icon={<ShieldCheck size={20} />} accentColor="#16A34A" />
          <StatCard title="Disputed" value={disputedCount} icon={<ShieldAlert size={20} />} accentColor="#DC2626" />
          <StatCard title="Flagged Claims" value={claims.length} icon={<MessageSquare size={20} />} accentColor="#1A4D8F" />
        </div>

        {error && (
          <div className="flex items-start gap-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
            <ShieldAlert size={16} className="mt-0.5 flex-shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError(null)} aria-label="Dismiss" className="text-red-500 hover:text-red-700">
              <X size={16} />
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
          {/* Operator console */}
          <div className="lg:col-span-3 page-card p-5 space-y-5">
            <div>
              <h3 className="mb-1">Send a verification alert</h3>
              <p className="text-sm text-gray-500">
                Ask the member to confirm a flagged claim in real time. A dispute escalates the
                claim to critical automatically.
              </p>
            </div>

            {isLoading ? (
              <p className="text-sm text-gray-400">Loading flagged claims…</p>
            ) : !activeClaim ? (
              <EmptyState title="No claims to verify" description="The queue is currently empty." />
            ) : (
              <>
                {/* Step 1 — claim */}
                <div>
                  <span className="block text-xs font-medium text-gray-600 mb-1.5">1 · Claim to verify</span>
                  <select
                    className="form-select"
                    value={activeClaim.claimRef}
                    onChange={(e) => {
                      setSelectedRef(e.target.value);
                      setAlert(null); setClaim(null); setResponse(null); setError(null);
                    }}
                  >
                    {claims.map((c) => (
                      <option key={c.claimRef} value={c.claimRef}>
                        {c.claimRef} · {c.member.name} · {formatCurrency(c.claimedAmount)} · {c.riskLevel}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Claim summary */}
                <div className="rounded-lg border border-gray-100 bg-gray-50 p-4 flex items-start gap-3">
                  <span className="w-9 h-9 rounded-full bg-brand-navy/10 text-brand-navy flex items-center justify-center text-xs font-bold flex-shrink-0">
                    {initials(activeClaim.member.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="font-semibold text-gray-800">{activeClaim.member.name}</p>
                      <RiskPill level={activeClaim.riskLevel} />
                    </div>
                    <p className="text-xs text-gray-500 mt-0.5 flex items-center gap-1">
                      <Phone size={11} />{' '}
                      {/* Queue rows carry no contact details; the alert response does. */}
                      {(activeClaim.member as { phone?: string }).phone ?? 'Number shown once the alert is sent'}
                    </p>
                    <p className="text-sm text-gray-600 mt-1">
                      {formatCurrency(activeClaim.claimedAmount)} at {activeClaim.provider.name}
                      {activeClaim.serviceDate ? ` · ${activeClaim.serviceDate}` : ''}
                    </p>
                    <div className="mt-2">
                      <span className={cn(
                        'badge',
                        activeClaim.memberResponse === 'CONFIRMED' ? 'bg-green-50 text-green-700 border-green-200'
                          : activeClaim.memberResponse === 'DISPUTED' ? 'bg-red-50 text-red-700 border-red-200'
                            : 'bg-amber-50 text-amber-700 border-amber-200'
                      )}>
                        {activeClaim.memberResponse === 'PENDING' ? 'Awaiting reply' : activeClaim.memberResponse}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Step 2 — channel */}
                <div>
                  <span className="block text-xs font-medium text-gray-600 mb-1.5">2 · Channel</span>
                  <div className="grid grid-cols-3 gap-2">
                    {CHANNELS.map((c) => {
                      const active = channel === c.key;
                      const Icon = c.icon;
                      return (
                        <button
                          key={c.key} type="button" onClick={() => setChannel(c.key)}
                          className={cn(
                            'flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors',
                            active ? 'border-brand-navy bg-brand-navy/5 text-brand-navy' : 'border-gray-200 text-gray-600 hover:border-gray-300'
                          )}
                          aria-pressed={active}
                        >
                          <Icon size={16} /> {c.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Step 3 — send */}
                <div className="flex items-center gap-3">
                  <button
                    className="btn-primary"
                    onClick={() => alertMutation.mutate()}
                    disabled={alertMutation.isPending}
                  >
                    {alertMutation.isPending ? <><RefreshCw size={16} className="animate-spin" /> Sending…</>
                      : <><Send size={16} /> {alert ? 'Resend alert' : 'Send alert'}</>}
                  </button>
                  {alert && (
                    <span className="text-xs text-gray-400">
                      {alert.delivered ? 'Delivered' : 'Queued (log-only in demo)'} · {alert.channel}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Phone preview */}
          <div className="lg:col-span-2 page-card p-5">
            <PhoneFrame
              claim={activeClaim}
              alert={alert}
              response={response}
              responding={responseMutation.isPending}
              onRespond={(r) => responseMutation.mutate(r)}
            />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
