import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { fetchLiveFeed } from '@/lib/api';
import { realtimeClient } from '@/lib/realtime';
import { RiskScoreRing } from '@/components/shared/RiskScoreRing';
import { StatusBadge } from '@/components/shared/Badges';
import { FlagBadgeList } from '@/components/shared/FlagBadge';
import { LatencyBadge } from '@/components/shared/LatencyBadge';
import { SkeletonTableBody } from '@/components/shared/SkeletonLoader';
import { LiveDot } from '@/components/shared/LiveDot';
import { formatCurrency, formatTimeAgo } from '@/lib/formatters';
import type { ClaimSummary } from '@/types';
import { Link } from '@tanstack/react-router';
import { ExternalLink } from 'lucide-react';

export function LiveClaimFeed() {
  const { data: initial, isLoading } = useQuery({
    queryKey: ['live-feed'],
    queryFn: fetchLiveFeed,
  });

  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [newClaimRef, setNewClaimRef] = useState<string | null>(null);

  useEffect(() => {
    if (initial) setClaims(initial);
  }, [initial]);

  useEffect(() => {
    const handler = (event: { type: string; payload: Record<string, unknown> }) => {
      if (event.type === 'claim_scored') {
        setNewClaimRef(event.payload.claimRef as string);
        setTimeout(() => setNewClaimRef(null), 3000);
      }
    };
    realtimeClient.on('claim_scored', handler as never);
    return () => realtimeClient.off('claim_scored', handler as never);
  }, []);

  return (
    <div className="page-card">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <LiveDot />
          <h2 className="text-sm font-semibold text-gray-800">Live Claim Scoring Feed</h2>
          <span className="badge bg-blue-50 text-blue-700 border-blue-100 ml-1">FraudShield AI</span>
        </div>
        <Link to="/queue" className="text-xs text-brand-navy hover:underline font-medium flex items-center gap-1">
          View all <ExternalLink size={10} />
        </Link>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="data-table" aria-label="Live claim scoring feed">
          <thead>
            <tr>
              <th>Score</th>
              <th>Ref</th>
              <th>Member</th>
              <th>Provider</th>
              <th>Amount</th>
              <th>Decision</th>
              <th>Latency</th>
              <th>Flags</th>
            </tr>
          </thead>
          {isLoading ? (
            <SkeletonTableBody rows={5} />
          ) : (
            <tbody>
              {claims.map((claim) => (
                <tr
                  key={claim.id}
                  className={claim.claimRef === newClaimRef ? 'bg-amber-50 border-l-2 border-l-amber-400' : ''}
                >
                  <td>
                    <RiskScoreRing score={claim.riskScore} size={52} strokeWidth={6} />
                  </td>
                  <td>
                    <Link
                      to="/queue/$claimRef"
                      params={{ claimRef: claim.claimRef }}
                      className="text-brand-navy font-semibold hover:underline text-sm"
                    >
                      {claim.claimRef}
                    </Link>
                    <p className="text-[10px] text-gray-400 mt-0.5">{formatTimeAgo(claim.submittedAt)}</p>
                  </td>
                  <td>
                    <p className="text-sm font-medium text-gray-800">{claim.member.name}</p>
                    <p className="text-[10px] text-gray-400">{claim.member.memberNumber}</p>
                  </td>
                  <td>
                    <p className="text-sm text-gray-700">{claim.provider.name}</p>
                    <p className="text-[10px] text-gray-400">{claim.provider.code}</p>
                  </td>
                  <td className="font-semibold text-gray-800">{formatCurrency(claim.claimedAmount)}</td>
                  <td><StatusBadge decision={claim.decision} /></td>
                  <td><LatencyBadge ms={claim.latencyMs} /></td>
                  <td>
                    <div className="max-w-xs">
                      <FlagBadgeList flags={claim.flags} />
                      {claim.flags.length > 0 && (
                        <p className="text-[10px] text-gray-400 mt-1 leading-relaxed line-clamp-2">
                          {claim.aiExplanation}
                        </p>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </div>
    </div>
  );
}
