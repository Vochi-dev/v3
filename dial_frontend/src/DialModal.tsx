import React, { useState, useEffect } from 'react';
import './DialModal.css';
import MusicModal from './MusicModal';
import { ManagerInfo } from './types';

interface MusicFile {
    id: number;
    display_name: string;
}

interface HoldMusicInfo {
    type: 'default' | 'none' | 'custom';
    id?: number;
    name?: string;
}

interface DialModalProps {
    enterpriseId: string;
    onClose: () => void;
    onConfirm: (data: any) => void;
    onAddManager: () => void;
    onDelete: () => void;
    onRemoveManager: (index: number) => void;
    managers: ManagerInfo[];
    initialData?: {
        holdMusic?: HoldMusicInfo;
        waitingRings?: number;
        [key: string]: any;
    };
}

const DialModal: React.FC<DialModalProps> = ({ 
    enterpriseId, 
    onClose, 
    onConfirm,
    onAddManager,
    onRemoveManager,
    onDelete,
    managers,
    initialData 
}) => {
    const [musicOption, setMusicOption] = useState<'default' | 'none' | 'custom'>('default');
    const [isMusicModalOpen, setMusicModalOpen] = useState(false);
    const [selectedMusicFile, setSelectedMusicFile] = useState<MusicFile | null>(null);
    const [waitingRings, setWaitingRings] = useState(3);

    useEffect(() => {
        if (initialData) {
            setWaitingRings(initialData.waitingRings || 3);
            if (initialData.holdMusic) {
                const music = initialData.holdMusic;
                setMusicOption(music.type || 'default');
                if (music.type === 'custom' && music.id && music.name) {
                    setSelectedMusicFile({ id: music.id, display_name: music.name });
                } else {
                    setSelectedMusicFile(null);
                }
            } else {
                setMusicOption('default');
                setSelectedMusicFile(null);
            }
        }
    }, [initialData]);

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

    const handleConfirm = () => {
        if (managers.length === 0) {
            alert("Нельзя сохранить узел 'Звонок на список' без сотрудников. Добавьте хотя бы одного сотрудника.");
            return;
        }
        
        let holdMusicData: HoldMusicInfo | null = { type: 'default' };
        if (musicOption === 'custom' && selectedMusicFile) {
            holdMusicData = { type: 'custom', id: selectedMusicFile.id, name: selectedMusicFile.display_name };
        } else if (musicOption === 'none') {
            holdMusicData = { type: 'none' };
        }

        onConfirm({
            managers: managers,
            holdMusic: holdMusicData,
            waitingRings: waitingRings,
        });
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
                    <div className="managers-table-container">
                        <table className="managers-table">
                            <thead>
                                <tr>
                                    <th className="name-column">Имя</th>
                                    <th className="phone-column">Телефон</th>
                                    <th className="action-column"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {managers.map((manager, index) => (
                                    <tr key={`${manager.phone}-${index}`}>
                                        <td className="name-column">{manager.name || 'Не назначен'}</td>
                                        <td className="phone-column">{manager.phone}</td>
                                        <td className="action-column">
                                            <button className="remove-manager-button" onClick={() => onRemoveManager(index)}>
                                                &times;
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <button className="add-manager-button" onClick={onAddManager}>
                        Добавить сотрудника
                    </button>
                    
                    <div className="music-options-container">
                        <span className="music-options-label">Музыка ожидания</span>
                        <div className="radio-option">
                            <input
                                type="radio"
                                id="music-default"
                                name="music"
                                value="default"
                                checked={musicOption === 'default'}
                                onChange={(e) => setMusicOption(e.target.value as 'default')}
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
                                onChange={(e) => setMusicOption(e.target.value as 'none')}
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
                                onChange={(e) => setMusicOption(e.target.value as 'custom')}
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
                    <button className="delete-button" onClick={onDelete}>Удалить</button>
                    <div className="footer-right-buttons">
                        <button className="cancel-button" onClick={onClose}>Отмена</button>
                        <button className="ok-button" onClick={handleConfirm}>ОК</button>
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