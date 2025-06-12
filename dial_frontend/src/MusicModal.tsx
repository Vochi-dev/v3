import React from 'react';
import './MusicModal.css';

interface MusicModalProps {
    onClose: () => void;
}

const MusicModal: React.FC<MusicModalProps> = ({ onClose }) => {
    return (
        <div className="music-modal-overlay">
            <div className="music-modal-content">
                <div className="music-modal-header">
                    <h2>Музыка в режиме ожидания</h2>
                </div>
                <div className="music-modal-body">
                    {/* Контент для загрузки и выбора файлов будет здесь */}
                </div>
                <div className="music-modal-footer">
                    <button onClick={onClose} className="close-button">Закрыть</button>
                </div>
            </div>
        </div>
    );
};

export default MusicModal; 