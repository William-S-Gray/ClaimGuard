import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { useEffect } from 'react';
import { ShieldCheck } from 'lucide-react';
import { queryClient } from '@/lib/queryClient';
import { router } from './router';
import { useWSStore } from '@/stores/wsStore';
import { useAuthStore } from '@/stores/authStore';
import { realtimeClient } from '@/lib/realtime';
import { ErrorBoundary } from '@/components/shared/ErrorBoundary';
import { LoginPage } from '@/routes/LoginPage';
import { ProviderPortalPage } from '@/routes/ProviderPortalPage';

function AppInner() {
  const { connect } = useWSStore();

  useEffect(() => {
    connect();
  }, [connect]);

  useEffect(() => {
    // Notifications are now generated and persisted server-side; the client just
    // refetches the relevant queries when realtime events arrive.
    const handleEvent = (event: { type: string; payload: Record<string, any> }) => {
      if (event.type === 'notification_sent') {
        queryClient.invalidateQueries({ queryKey: ['notifications'] });
      }
      if (event.type === 'queue_updated' || event.type === 'claim_scored') {
        queryClient.invalidateQueries({ queryKey: ['queue'] });
        queryClient.invalidateQueries({ queryKey: ['live-feed'] });
      }
      if (event.type === 'dashboard_updated') {
        queryClient.invalidateQueries({ queryKey: ['dashboard-metrics'] });
      }
      if (event.type === 'trustscore_updated') {
        queryClient.invalidateQueries({ queryKey: ['providers'] });
      }
    };

    realtimeClient.on('*', handleEvent as any);
    return () => realtimeClient.off('*', handleEvent as any);
  }, []);

  return (
    <ErrorBoundary>
      <RouterProvider router={router} />
    </ErrorBoundary>
  );
}

function AuthSplash() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50 gap-3">
      <div className="w-12 h-12 rounded-2xl bg-brand-navy text-white flex items-center justify-center animate-pulse">
        <ShieldCheck size={24} />
      </div>
      <p className="text-sm text-gray-400">Loading ClaimGuard…</p>
    </div>
  );
}

function AuthGate() {
  const { status, bootstrap, user } = useAuthStore();

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  if (status === 'loading') return <AuthSplash />;
  if (status === 'unauthenticated') return <LoginPage />;
  // Provider accounts get only their portal: no staff router, no live feed.
  if (user?.providerId) return <ProviderPortalPage />;
  return <AppInner />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate />
    </QueryClientProvider>
  );
}
