import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Star } from 'lucide-react';

interface LeaderboardTabProps {
  apiBase: string;
  token: string;
}

interface UserProfile {
  id: number;
  telegram_id: number;
  username: string | null;
  full_name: string | null;
  score: number;
  stats: Record<string, number>;
}

interface LeaderboardEntry {
  position: number;
  user_id: number;
  telegram_id: number;
  name: string;
  username: string | null;
  value: number;
}

export const LeaderboardTab: React.FC<LeaderboardTabProps> = ({ apiBase, token }) => {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [filter, setFilter] = useState<'score' | 'posts' | 'tournaments'>('score');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Load profile
    axios
      .get(`${apiBase}/profile/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => setProfile(res.data))
      .catch((err) => console.error("Failed to load profile", err));
  }, [apiBase, token]);

  useEffect(() => {
    setLoading(true);
    axios
      .get(`${apiBase}/leaderboard?type=${filter}`, { headers: { Authorization: `Bearer ${token}` } })
      .then((res) => setLeaderboard(res.data))
      .catch((err) => console.error("Failed to load leaderboard", err))
      .finally(() => setLoading(false));
  }, [apiBase, token, filter]);

  const getMedal = (position: number) => {
    if (position === 1) return '🥇';
    if (position === 2) return '🥈';
    if (position === 3) return '🥉';
    return `#${position}`;
  };

  return (
    <div className="animate-fade-in" style={{ padding: 16 }}>
      {/* User Profile Card */}
      {profile && (
        <div className="glass-panel" style={{ padding: 20, marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div
              style={{
                width: 52,
                height: 52,
                borderRadius: '50%',
                background: 'var(--accent-gradient)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 22,
                fontWeight: 700,
                color: 'white',
              }}
            >
              {profile.full_name ? profile.full_name[0].toUpperCase() : '👤'}
            </div>
            <div>
              <h3 style={{ fontSize: 17, fontWeight: 700 }}>{profile.full_name || `@${profile.username}` || 'Пользователь'}</h3>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                <span className="badge badge-amber">
                  <Star size={14} fill="#fbbf24" /> {profile.score} очков
                </span>
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 16, textAlign: 'center' }}>
            <div style={{ background: 'rgba(15, 23, 42, 0.5)', padding: 10, borderRadius: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--hint-color)' }}>Одобрено</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#34d399' }}>{profile.stats.APPROVED || 0}</div>
            </div>
            <div style={{ background: 'rgba(15, 23, 42, 0.5)', padding: 10, borderRadius: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--hint-color)' }}>В очереди</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#fbbf24' }}>{profile.stats.PENDING || 0}</div>
            </div>
            <div style={{ background: 'rgba(15, 23, 42, 0.5)', padding: 10, borderRadius: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--hint-color)' }}>Опубликовано</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#c084fc' }}>{profile.stats.PUBLISHED || 0}</div>
            </div>
          </div>
        </div>
      )}

      {/* Leaderboard Tabs */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ fontSize: 16, fontWeight: 700 }}>Топ участников</h3>
        <div style={{ display: 'flex', gap: 4, background: 'rgba(30, 41, 59, 0.7)', padding: 4, borderRadius: 12 }}>
          <button
            onClick={() => setFilter('score')}
            style={{
              background: filter === 'score' ? '#a855f7' : 'transparent',
              color: 'white',
              border: 'none',
              padding: '4px 10px',
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Очки
          </button>
          <button
            onClick={() => setFilter('posts')}
            style={{
              background: filter === 'posts' ? '#a855f7' : 'transparent',
              color: 'white',
              border: 'none',
              padding: '4px 10px',
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Посты
          </button>
          <button
            onClick={() => setFilter('tournaments')}
            style={{
              background: filter === 'tournaments' ? '#a855f7' : 'transparent',
              color: 'white',
              border: 'none',
              padding: '4px 10px',
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Победы
          </button>
        </div>
      </div>

      {/* Leaderboard List */}
      <div className="glass-panel" style={{ padding: 8 }}>
        {loading ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--hint-color)' }}>Загрузка...</div>
        ) : leaderboard.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--hint-color)' }}>Список пуст</div>
        ) : (
          leaderboard.map((entry) => (
            <div
              key={entry.user_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 14px',
                borderRadius: 12,
                marginBottom: 4,
                background: entry.telegram_id === profile?.telegram_id ? 'rgba(168, 85, 247, 0.15)' : 'transparent',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 16, fontWeight: 700, width: 28, textAlign: 'center' }}>
                  {getMedal(entry.position)}
                </span>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{entry.name}</div>
                  {entry.username && <div style={{ fontSize: 11, color: 'var(--hint-color)' }}>@{entry.username}</div>}
                </div>
              </div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#38bdf8' }}>
                {entry.value} {filter === 'score' ? 'очков' : filter === 'posts' ? 'постов' : 'побед'}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
