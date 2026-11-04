import { useQuery } from '@tanstack/react-query';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';
import { fetchSavingsData } from '@/lib/api';
import { formatCurrency } from '@/lib/formatters';

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-gray-100 rounded-xl shadow-lg p-3 text-xs">
      <p className="font-semibold text-gray-700 mb-2">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 mb-1">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-gray-500">{p.name}:</span>
          <span className="font-semibold text-gray-800">{formatCurrency(p.value)}</span>
        </div>
      ))}
    </div>
  );
};

export function SavingsChart() {
  // Real monthly figures from the claims; months with nothing saved stay on the axis as zero.
  const { data = [], isLoading } = useQuery({ queryKey: ['savings'], queryFn: fetchSavingsData });

  if (isLoading) {
    return (
      <div className="page-card p-5">
        <div className="h-72 flex items-center justify-center">
          <div className="skeleton h-56 w-full rounded-xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="page-card p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-800">Loss Avoided — Last 12 Months</h2>
          <p className="text-xs text-gray-400 mt-0.5">Claimed amounts rejected or held for investigation, by month submitted</p>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="savingsGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#1A4D8F" stopOpacity={0.15} />
              <stop offset="95%" stopColor="#1A4D8F" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="cumulativeGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#16A34A" stopOpacity={0.12} />
              <stop offset="95%" stopColor="#16A34A" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={(v: number) => (v >= 1000 ? `$${v / 1000}k` : `$${v}`)} tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={false} tickLine={false} />
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 12 }} />
          <Area type="monotone" dataKey="savings" name="Monthly savings" stroke="#1A4D8F" strokeWidth={2} fill="url(#savingsGrad)" />
          <Area type="monotone" dataKey="cumulative" name="Running total" stroke="#16A34A" strokeWidth={2} fill="url(#cumulativeGrad)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
