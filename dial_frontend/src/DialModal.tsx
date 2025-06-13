import React, { useState } from 'react';
import './DialModal.css';
import MusicModal from './MusicModal';

// Этот тип должен совпадать с FlattenedManager в AddManagerModal
interface ManagerInfo {
    userId: number;
    name: string;
    phone: string;
}

interface MusicFile {
    id: number;
    display_name: string;
}

interface DialModalProps {
    enterpriseId: string;
    onClose: () => void;
    onAddManagerClick: () => void;
    addedManagers: ManagerInfo[];
    onRemoveManager: (index: number) => void;
}

const DialModal: React.FC<DialModalProps> = ({ enterpriseId, onClose, onAddManagerClick, addedManagers, onRemoveManager }) => {
    const [musicOption, setMusicOption] = useState('default');
    const [isMusicModalOpen, setMusicModalOpen] = useState(false);
    const [selectedMusicFile, setSelectedMusicFile] = useState<MusicFile | null>(null);
    const [waitingRings, setWaitingRings] = useState(3);

    const handleSelectMusic = (file: MusicFile) => {
        setSelectedMusicFile(file);
    };

    const handleWaitingRingsChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        let value = parseInt(e.target.value, 10);
        if (isNaN(value)) {
            value = 1; // или setWaitingRings('')
        } else if (value < 1) {
            value = 1;
        } else if (value > 15) {
            value = 15;
        }
        setWaitingRings(value);
    };

    return (
        <div className="dial-modal-overlay" onClick={e => {
            if (e.target === e.currentTarget) onClose();
        }}>
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
                                <th className="action-column"></th>
                            </tr>
                        </thead>
                        <tbody>
                            {addedManagers.map((manager, index) => (
                                <tr key={`${manager.userId}-${manager.phone}-${index}`}>
                                    <td>{manager.phone}</td>
                                    <td>{manager.name}</td>
                                    <td>
                                        <button 
                                            onClick={() => onRemoveManager(index)}
                                            className="remove-manager-button"
                                        >
                                            &times;
                                        </button>
                                    </td>
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
                                <>
                                    <button 
                                        className="choose-file-button"
                                        onClick={(e) => {
                                            e.preventDefault();
                                            setMusicModalOpen(true);
                                        }}
                                    >
                                        Выберите файл
                                    </button>
                                    {selectedMusicFile && (
                                        <span className="selected-file-name">{selectedMusicFile.display_name}</span>
                                    )}
                                </>
                            )}
                        </div>
                    </div>

                    <div className="waiting-time-container">
                        <label htmlFor="waiting-time">Время ожидания сотрудника</label>
                        <input
                            type="number"
                            id="waiting-time"
                            name="waiting-time"
                            value={waitingRings}
                            onChange={handleWaitingRingsChange}
                            min="1"
                            max="15"
                        />
                        <span>гудков</span>
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
                <MusicModal 
                    enterpriseId={enterpriseId}
                    onClose={() => setMusicModalOpen(false)} 
                    onSelect={handleSelectMusic}
                    modalTitle="Музыка в режиме ожидания"
                    fileType="hold"
                />
            )}
        </div>
    );
};

export default DialModal; 