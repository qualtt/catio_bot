import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Trophy, CheckCircle2, Sparkles, Flame, RefreshCw } from 'lucide-react';

interface TournamentTabProps {
  apiBase: string;
  token: string;
}

interface MatchEntry {
  id: number;
  photo_id: number;
  image_url: string;
}

interface ActiveMatch {
  match_id: number;
  round_number: number;
  match_number: number;
  left_entry: MatchEntry;
  right_entry: MatchEntry;
}

interface TournamentInfo {
  id: number;
  type: string;
  status: string;
  period_label: string;
  voter_count: number;
  active_match: ActiveMatch | null;
  champion_photo_id: number | null;
  results_summary: string | null;
}

export const TournamentTab: React.FC<TournamentTabProps> = ({ apiBase, token }) => {
  const [data, setData] = useState<TournamentInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [voting, setVoting] = useState(false);
  const [votedSide, setVotedSide] = useState<'left' | 'right' | null>(null);

  const fetchTournament = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${apiBase}/tournaments/active`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setData(res.data);
      setVotedSide(null);
    } catch (err) {
      console.error("Failed to load tournament data", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTournament();
  }, [apiBase, token]);

  const handleVote = async (side: 'left' | 'right', entryId: number) => {
    if (!data?.active_match || voting) return;
    setVoting(true);
    setVotedSide(side);

    // Trigger Telegram Haptic Feedback if available
    if (window.Telegram?.WebApp?.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.impactOccurred('medium');
    }

    try {
      await axios.post(
        `${apiBase}/tournaments/vote`,
        {
          match_id: data.active_match.match_id,
          chosen_entry_id: entryId,
        },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setTimeout(() => {
        fetchTournament();
      }, 600);
    } catch (err) {
      console.error("Failed to submit vote", err);
      setVoting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <RefreshCw className="animate-spin" size={32} color="#a855f7" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="glass-panel" style={{ padding: 24, margin: 16, textAlign: 'center' }}>
        <Trophy size={48} color="#94a3b8" style={{ marginBottom: 12 }} />
        <h3 style={{ marginBottom: 8 }}>Нет активных турниров</h3>
        <p style={{ color: 'var(--hint-color)', fontSize: 14 }}>
          Следите за новостями в канале, еженедельные кубки стартуют автоматически!
        </p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in" style={{ padding: 16 }}>
      {/* Header Banner */}
      <div className="glass-panel" style={{ padding: 16, marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <span className="badge badge-purple">
              <Flame size={14} /> {data.type === 'weekly' ? 'Еженедельный кубок' : 'Ежемесячный кубок'}
            </span>
            <span className="badge badge-amber">{data.period_label}</span>
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>Турнирный Плей-офф</h2>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 12, color: 'var(--hint-color)' }}>Голосовало</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: '#38bdf8' }}>{data.voter_count} 👥</div>
        </div>
      </div>

      {/* Active Match Voting */}
      {data.active_match ? (
        <div>
          <div style={{ textAlign: 'center', marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--hint-color)', textTransform: 'uppercase', letterSpacing: 1 }}>
              Раунд {data.active_match.round_number} • Матч {data.active_match.match_number}
            </span>
            <h3 style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>Кто милее? Нажми для выбора!</h3>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {/* Left Photo */}
            <div
              className={`glass-panel ${votedSide === 'left' ? 'pulse-active' : ''}`}
              onClick={() => handleVote('left', data.active_match!.left_entry.id)}
              style={{
                position: 'relative',
                overflow: 'hidden',
                borderRadius: 20,
                cursor: 'pointer',
                border: votedSide === 'left' ? '2px solid #a855f7' : '1px solid var(--glass-border)',
                transition: 'all 0.2s ease',
              }}
            >
              <img
                src={`${apiBase}/photos/${data.active_match.left_entry.photo_id}/image`}
                alt="Участник 1"
                style={{ width: '100%', height: '220px', objectFit: 'cover' }}
              />
              <div style={{ padding: 10, textAlign: 'center', background: 'rgba(15, 23, 42, 0.8)' }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>Фото #{data.active_match.left_entry.photo_id}</span>
              </div>
            </div>

            {/* Right Photo */}
            <div
              className={`glass-panel ${votedSide === 'right' ? 'pulse-active' : ''}`}
              onClick={() => handleVote('right', data.active_match!.right_entry.id)}
              style={{
                position: 'relative',
                overflow: 'hidden',
                borderRadius: 20,
                cursor: 'pointer',
                border: votedSide === 'right' ? '2px solid #a855f7' : '1px solid var(--glass-border)',
                transition: 'all 0.2s ease',
              }}
            >
              <img
                src={`${apiBase}/photos/${data.active_match.right_entry.photo_id}/image`}
                alt="Участник 2"
                style={{ width: '100%', height: '220px', objectFit: 'cover' }}
              />
              <div style={{ padding: 10, textAlign: 'center', background: 'rgba(15, 23, 42, 0.8)' }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>Фото #{data.active_match.right_entry.photo_id}</span>
              </div>
            </div>
          </div>
        </div>
      ) : data.champion_photo_id ? (
        <div className="glass-panel" style={{ padding: 24, textAlign: 'center' }}>
          <Sparkles size={48} color="#fbbf24" style={{ marginBottom: 8 }} />
          <h2 className="gradient-text" style={{ fontSize: 22, fontWeight: 800, marginBottom: 8 }}>
            Вы проголосовали во всех доступных матчах!
          </h2>
          <p style={{ fontSize: 14, color: 'var(--hint-color)', marginBottom: 16 }}>
            Ваш выбор в этом раунде учтен. Победители раунда определятся после закрытия таймера.
          </p>
          <img
            src={`${apiBase}/photos/${data.champion_photo_id}/image`}
            alt="Ваш фаворит"
            style={{ width: '100%', maxHeight: '250px', objectFit: 'cover', borderRadius: 16 }}
          />
        </div>
      ) : (
        <div className="glass-panel" style={{ padding: 24, textAlign: 'center' }}>
          <CheckCircle2 size={48} color="#34d399" style={{ marginBottom: 8 }} />
          <h3 style={{ fontSize: 18, fontWeight: 700 }}>Результаты турнира</h3>
          <p style={{ fontSize: 14, color: 'var(--hint-color)', marginTop: 8, whiteSpace: 'pre-line' }}>
            {data.results_summary || 'Турнир завершен! Поздравляем победителей!'}
          </p>
        </div>
      )}
    </div>
  );
};
