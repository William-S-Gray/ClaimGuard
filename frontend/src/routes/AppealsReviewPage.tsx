import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { CheckCircle2, Filter, Scale, XCircle } from 'lucide-react';
import { fetchAppeals, resolveAppeal, type StaffAppeal } from '@/lib/api';
import { ApiError } from '@/lib/apiClient';
import { AppShell } from '@/components/layout/AppShell';
import { StatCard } from '@/components/shared/StatCard';
import { EmptyState, ErrorState } from '@/components/shared/EmptyState';
import { useAuthStore } from '@/stores/authStore';
import { formatDate } from '@/lib/formatters';

const FILTERS = ['OPEN', 'UPHELD', 'REJECTED', 'ALL'] as const;
type FilterKey = (typeof FILTERS)[number];
const FILTER_LABELS: Record<FilterKey, string> = {
  OPEN: 'Open',
  UPHELD: 'Upheld',
  REJECTED: 'Not upheld',
  ALL: 'All',
};
const STATUS_STYLE: Record<StaffAppeal['status'], string> = {
  OPEN: 'bg-amber-50 text-amber-700',
  UPHELD: 'bg-green-50 text-green-700',
  REJECTED: 'bg-gray-100 text-gray-700',
};

function Decision({ appeal }: { appeal: StaffAppeal }) {
  const qc = useQueryClient();
  const [notes, setNotes] = useState('');
  const decide = useMutation({
    mutationFn: (outcome: 'UPHELD' | 'REJECTED') => resolveAppeal(appeal.id, outcome, notes.trim()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['appeals'] }),
  });
  const notesId = `notes-${appeal.id}`;
  const ready = notes.trim().length > 0 && !decide.isPending;

  return (
    <div className="mt-3 border-t border-gray-100 pt-3">
      <label htmlFor={notesId} className="block text-xs font-medium text-gray-600 mb-1">
        Decision notes (the provider sees these)
      </label>
      <textarea
        id={notesId}
        className="form-input min-h-[64px] resize-y"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        maxLength={2000}
      />
      {decide.isError && (
        <p className="text-xs text-red-600 mt-1" role="alert">
          {decide.error instanceof ApiError ? decide.error.message : 'Could not save the decision.'}
        </p>
      )}
      <div className="flex flex-wrap gap-2 mt-2">
        <button
          type="button"
          className="btn-primary inline-flex items-center gap-1"
          disabled={!ready}
          onClick={() => decide.mutate('UPHELD')}
        >
          <CheckCircle2 size={14} aria-hidden /> Uphold
        </button>
        <button
          type="button"
          className="btn-secondary inline-flex items-center gap-1"
          disabled={!ready}
          onClick={() => decide.mutate('REJECTED')}
        >
          <XCircle size={14} aria-hidden /> Not upheld
        </button>
      </div>
      <p className="text-[11px] text-gray-400 mt-2">
        Upholding records the decision only. If the score itself should change, correct the
        underlying claims and recalculate it from the provider's page.
      </p>
    </div>
  );
}

function AppealCard({ appeal, canDecide }: { appeal: StaffAppeal; canDecide: boolean }) {
  return (
    <li className="page-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <Link
            to="/trustscore/$providerCode"
            params={{ providerCode: appeal.providerCode }}
            className="font-semibold text-brand-navy hover:underline"
          >
            {appeal.providerName}
          </Link>
          <p className="text-xs text-gray-500">
            {appeal.providerCode} · submitted {formatDate(appeal.submittedAt)} · score then{' '}
            <span className="font-semibold text-gray-700">{appeal.scoreAtSubmission}</span>
          </p>
        </div>
        <span className={`badge ${STATUS_STYLE[appeal.status]}`}>
          {appeal.status === 'OPEN' ? 'Open' : appeal.status === 'UPHELD' ? 'Upheld' : 'Not upheld'}
        </span>
      </div>

      <p className="text-sm text-gray-700 mt-3 whitespace-pre-line">{appeal.reason}</p>

      {appeal.status !== 'OPEN' && (
        <div className="mt-3 rounded bg-gray-50 p-3 text-sm">
          <p className="text-xs text-gray-500 mb-1">
            Decided {appeal.resolvedAt ? formatDate(appeal.resolvedAt) : ''}
          </p>
          <p className="text-gray-800">{appeal.outcomeNotes}</p>
        </div>
      )}

      {appeal.status === 'OPEN' && canDecide && <Decision appeal={appeal} />}
    </li>
  );
}

export function AppealsReviewPage() {
  const [filter, setFilter] = useState<FilterKey>('OPEN');
  const user = useAuthStore((s) => s.user);
  const canDecide = !!user && (user.isSuperuser || user.permissions.includes('provider:manage'));

  const { data: appeals = [], isLoading, isError } = useQuery({
    queryKey: ['appeals'],
    queryFn: fetchAppeals,
  });

  const count = (s: StaffAppeal['status']) => appeals.filter((a) => a.status === s).length;
  const shown = filter === 'ALL' ? appeals : appeals.filter((a) => a.status === filter);

  return (
    <AppShell title="TrustScore Appeals" subtitle="Providers disputing their score: review and decide">
      <div className="max-w-screen-lg mx-auto space-y-5">
        <div className="grid grid-cols-3 gap-4">
          <StatCard title="Open" value={count('OPEN')} icon={<Scale size={20} />} accentColor="#D97706" />
          <StatCard title="Upheld" value={count('UPHELD')} icon={<CheckCircle2 size={20} />} accentColor="#16A34A" />
          <StatCard title="Not upheld" value={count('REJECTED')} icon={<XCircle size={20} />} accentColor="#6B7280" />
        </div>

        <div className="page-card p-4 flex flex-wrap items-center gap-2">
          <Filter size={13} className="text-gray-400" aria-hidden="true" />
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 text-xs rounded-full font-medium transition-colors ${
                filter === f ? 'bg-brand-navy text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
              aria-pressed={filter === f}
            >
              {FILTER_LABELS[f]}
            </button>
          ))}
          {!canDecide && (
            <span className="text-xs text-gray-400 ml-auto">
              View only: deciding needs the provider-management permission.
            </span>
          )}
        </div>

        {isError ? (
          <ErrorState />
        ) : isLoading ? (
          <div className="skeleton h-32 rounded-xl" />
        ) : shown.length === 0 ? (
          <EmptyState
            title={filter === 'OPEN' ? 'No open appeals' : 'No appeals here'}
            description="Providers appeal from their portal; new appeals show up in Open."
            icon={<Scale className="text-gray-400" size={24} />}
          />
        ) : (
          <ul className="space-y-4">
            {shown.map((a) => (
              <AppealCard key={a.id} appeal={a} canDecide={canDecide} />
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
