import React from 'react';
import { Trophy, Upload, Award, ShieldAlert } from 'lucide-react';

interface NavbarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  isAdmin: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({ activeTab, setActiveTab, isAdmin }) => {
  const tabs = [
    { id: 'tournament', label: 'Турниры', icon: Trophy },
    { id: 'suggest', label: 'Загрузка', icon: Upload },
    { id: 'leaderboard', label: 'Лидеры', icon: Award },
  ];

  if (isAdmin) {
    tabs.push({ id: 'admin', label: 'Админка', icon: ShieldAlert });
  }

  return (
    <nav className="bottom-nav">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            className={`nav-item ${isActive ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <div className="icon-container">
              <Icon size={20} />
            </div>
            <span>{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
};
