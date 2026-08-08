import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Navbar } from './components/Navbar';
import { TournamentTab } from './components/TournamentTab';
import { SuggestTab } from './components/SuggestTab';
import { LeaderboardTab } from './components/LeaderboardTab';
import { AdminTab } from './components/AdminTab';

// Telegram WebApp Window Augmentation
declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string;
        initDataUnsafe?: any;
        ready: () => void;
        expand: () => void;
        themeParams?: any;
        HapticFeedback?: {
          impactOccurred: (style: string) => void;
          notificationOccurred: (type: string) => void;
        };
      };
    };
  }
}

const API_BASE = '/api/v1';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState('tournament');
  const [token, setToken] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [authLoading, setAuthLoading] = useState(true);

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
    }

    const initData = tg?.initData || '';

    // If running in Telegram WebApp context
    if (initData) {
      axios
        .post(`${API_BASE}/auth/telegram`, { init_data: initData })
        .then((res) => {
          setToken(res.data.token);
          setIsAdmin(res.data.user.is_admin);
        })
        .catch((err) => console.error("Telegram auth failed", err))
        .finally(() => setAuthLoading(false));
    } else if (import.meta.env.DEV) {
      // Local dev fallback when running local dev server (npm run dev)
      axios
        .post(`${API_BASE}/auth/dev-login`)
        .then((res) => {
          setToken(res.data.token);
          setIsAdmin(res.data.user.is_admin);
        })
        .catch((err) => console.error("Dev login failed", err))
        .finally(() => setAuthLoading(false));
    } else {
      setAuthLoading(false);
    }
  }, []);

  if (authLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', background: 'var(--bg-color)' }}>
        <div style={{ textAlign: 'center' }}>
          <h3 style={{ fontSize: 16, color: 'var(--hint-color)' }}>Загрузка Catio...</h3>
        </div>
      </div>
    );
  }

  // If outside Telegram WebApp in production without initData
  if (!token && !import.meta.env.DEV) {
    return (
      <div style={{ maxWidth: 480, margin: '0 auto', minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
        <div className="glass-panel" style={{ padding: 30, textAlign: 'center' }}>
          <h2 className="gradient-text" style={{ fontSize: 24, fontWeight: 800, marginBottom: 12 }}>Catio Mini App</h2>
          <p style={{ fontSize: 14, color: 'var(--hint-color)', marginBottom: 20 }}>
            Пожалуйста, откройте это приложение через Telegram бота для авторизации.
          </p>
          <a
            href="https://t.me/CatioBot"
            target="_blank"
            rel="noreferrer"
            className="btn-primary"
            style={{ textDecoration: 'none', display: 'inline-flex' }}
          >
            Перейти в Telegram Бота ✈️
          </a>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 480, margin: '0 auto', minHeight: '100vh', position: 'relative' }}>
      <header style={{ padding: '16px 16px 8px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 className="gradient-text" style={{ fontSize: 22, fontWeight: 800 }}>Catio</h1>
        </div>
        {isAdmin && <span className="badge badge-emerald">Admin Mode</span>}
      </header>

      <main>
        {activeTab === 'tournament' && <TournamentTab apiBase={API_BASE} token={token || ''} />}
        {activeTab === 'suggest' && <SuggestTab apiBase={API_BASE} token={token || ''} />}
        {activeTab === 'leaderboard' && <LeaderboardTab apiBase={API_BASE} token={token || ''} />}
        {activeTab === 'admin' && isAdmin && <AdminTab apiBase={API_BASE} token={token || ''} />}
      </main>

      <Navbar activeTab={activeTab} setActiveTab={setActiveTab} isAdmin={isAdmin} />
    </div>
  );
};

export default App;
