import { useQuery } from '@tanstack/react-query';
import { fetchDashboardMetrics } from '@/lib/api';
import { StatCard } from '@/components/shared/StatCard';
import { SkeletonCard } from '@/components/shared/SkeletonLoader';
import { ErrorState } from '@/components/shared/EmptyState';
import { FileText, AlertTriangle, DollarSign, MessageCircleWarning } from 'lucide-react';
import { formatCurrency, formatNumber } from '@/lib/formatters';

export function MetricCards() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dashboard-metrics'],
    queryFn: fetchDashboardMetrics,
    refetchInterval: 15000,
  });

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
      </div>
    );
  }

  if (isError || !data) {
    return <ErrorState onRetry={refetch} />;
  }

  // Compared with yesterday's real count; there is nothing to compare when yesterday had none.
  const change =
    data.claimsYesterday > 0
      ? Math.round(((data.claimsToday - data.claimsYesterday) / data.claimsYesterday) * 100)
      : null;

  const cards = [
    {
      title: 'Claims Today',
      value: formatNumber(data.claimsToday),
      subtitle: `${formatNumber(data.autoApprovedToday)} auto-approved`,
      icon: <FileText size={20} />,
      accentColor: '#1A4D8F',
      trend: change === null
        ? { value: 'None yesterday to compare', positive: true }
        : { value: `${change >= 0 ? '+' : ''}${change}% vs yesterday`, positive: change >= 0 },
    },
    {
      title: 'Flagged',
      value: formatNumber(data.flaggedToday),
      subtitle: `${data.pendingInvestigation} under investigation`,
      icon: <AlertTriangle size={20} />,
      accentColor: '#DC2626',
      trend: {
        value: data.claimsToday > 0
          ? `${((data.flaggedToday / data.claimsToday) * 100).toFixed(1)}% flag rate`
          : 'No claims yet today',
        positive: false,
      },
    },
    {
      title: 'Estimated Saved',
      value: formatCurrency(data.estimatedSaved),
      subtitle: 'Claims rejected or held for investigation',
      icon: <DollarSign size={20} />,
      accentColor: '#16A34A',
      trend: { value: 'All time', positive: true },
    },
    {
      title: 'Member Alerts',
      value: String(data.memberAlerts),
      subtitle: 'Claims disputed by members',
      icon: <MessageCircleWarning size={20} />,
      accentColor: '#7B2D8B',
      trend: { value: 'WhatsApp · USSD · App', positive: true },
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      {cards.map((card) => (
        <StatCard key={card.title} {...card} />
      ))}
    </div>
  );
}
