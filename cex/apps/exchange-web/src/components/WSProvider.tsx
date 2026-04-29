import { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { useWSStore } from '../lib/ws-store';
import { useAuthStore } from '../lib/auth-store';

export function WSProvider({ children }: { children: React.ReactNode }) {
  const { connect, disconnect, connectionState } = useWSStore();
  const { isAuthenticated, user } = useAuthStore();

  useEffect(() => {
    // Keep websocket disconnected for public/unauthenticated views.
    if (!isAuthenticated || !user?.id) {
      disconnect();
      return;
    }

    connect(user.id);

    return () => {
      disconnect();
    };
  }, [connect, disconnect, isAuthenticated, user]);

  return (
    <>
      {children}
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: {
            background: '#1e293b',
            color: '#f8fafc',
            border: '1px solid #334155',
          },
          success: {
            iconTheme: {
              primary: '#10b981',
              secondary: '#f8fafc',
            },
          },
          error: {
            iconTheme: {
              primary: '#ef4444',
              secondary: '#f8fafc',
            },
          },
        }}
      />
      
      {/* Connection status indicator */}
      {connectionState === 'reconnecting' && (
        <div className="fixed bottom-4 right-4 bg-yellow-600 text-white px-4 py-2 rounded-lg shadow-lg">
          Reconnecting...
        </div>
      )}
      {connectionState === 'error' && (
        <div className="fixed bottom-4 right-4 bg-red-600 text-white px-4 py-2 rounded-lg shadow-lg">
          Connection error
        </div>
      )}
    </>
  );
}
