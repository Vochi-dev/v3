import React, { useState } from 'react';
import './DialModal.css';
import MusicModal from './MusicModal';

// Этот тип должен совпадать с FlattenedManager в AddManagerModal
interface ManagerInfo {
    userId: number;
    name: string;
    phone: string;
}

interface DialModalProps {
    onClose: () => void;
    onAddManagerClick: () => void;
    addedManagers: ManagerInfo[];
}

const DialModal: React.FC<DialModalProps> = ({ onClose, onAddManagerClick, addedManagers }) => {
    const [musicOption, setMusicOption] = useState('default');
    const [isMusicModalOpen, setMusicModalOpen] = useState(false);

    return (
        <div className="dial-modal-overlay" onClick={onClose}>
            <div className="dial-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="dial-modal-header">
                    <h3>Звонок на список</h3>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="dial-modal-body">
                    <table className="dial-table">
                        <thead>
                            <tr>
                                <th>Номер</th>
                                <th>Имя</th>
                            </tr>
                        </thead>
                        <tbody>
                            {addedManagers.map((manager, index) => (
                                <tr key={`${manager.userId}-${manager.phone}-${index}`}>
                                    <td>{manager.phone}</td>
                                    <td>{manager.name}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <button className="add-manager-button" onClick={onAddManagerClick}>Добавить менеджера</button>
                    
                    <div className="music-options-container">
                        <span className="music-options-label">Музыка ожидания</span>
                        <div className="radio-option">
                            <input
                                type="radio"
                                id="music-default"
                                name="music"
                                value="default"
                                checked={musicOption === 'default'}
                                onChange={(e) => setMusicOption(e.target.value)}
                            />
                            <label htmlFor="music-default">По умолчанию</label>
                        </div>
                        <div className="radio-option">
                            <input
                                type="radio"
                                id="music-none"
                                name="music"
                                value="none"
                                checked={musicOption === 'none'}
                                onChange={(e) => setMusicOption(e.target.value)}
                            />
                            <label htmlFor="music-none">Без музыки</label>
                        </div>
                        <div className="radio-option">
                            <input
                                type="radio"
                                id="music-custom"
                                name="music"
                                value="custom"
                                checked={musicOption === 'custom'}
                                onChange={(e) => setMusicOption(e.target.value)}
                            />
                            <label htmlFor="music-custom">Своя</label>
                            {musicOption === 'custom' && (
                                <button 
                                    className="choose-file-button"
                                    onClick={(e) => {
                                        e.preventDefault();
                                        setMusicModalOpen(true);
                                    }}
                                >
                                    Выберите файл
                                </button>
                            )}
                        </div>
                    </div>

                </div>
                <div className="dial-modal-footer">
                    <div className="footer-buttons-left">
                        <button className="delete-button">Удалить</button>
                    </div>
                    <div className="footer-buttons-right">
                        <button className="cancel-button" onClick={onClose}>Отмена</button>
                        <button className="ok-button">ОК</button>
                    </div>
                </div>
            </div>

            {isMusicModalOpen && (
                <MusicModal onClose={() => setMusicModalOpen(false)} />
            )}
        </div>
    );
};

export default DialModal; 