import React, { useState, useEffect } from 'react';
import './PatternCheckModal.css';
import AddPatternModal from './AddPatternModal';

// Локально определяем тип, чтобы не трогать глобальные файлы
interface Pattern {
    id: number;
    name: string;
    shablon: string;
}

interface PatternCheckModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSave: (patterns: Pattern[]) => void; 
    onDelete?: () => void;
    initialPatterns: Pattern[];
}

const PatternCheckModal: React.FC<PatternCheckModalProps> = ({ isOpen, onClose, onSave, onDelete, initialPatterns }) => {
    const [isAddPatternModalOpen, setAddPatternModalOpen] = useState(false);
    const [patterns, setPatterns] = useState<Pattern[]>([]);

    useEffect(() => {
        if (isOpen) {
            setPatterns(initialPatterns || []);
        }
    }, [isOpen, initialPatterns]);

    const handleAddPatternClick = () => {
        setAddPatternModalOpen(true);
    };

    const handleCloseAddPatternModal = () => {
        setAddPatternModalOpen(false);
    };

    const handleConfirmAddPattern = (selectedPatterns: Pattern[]) => {
        // Просто устанавливаем новый список выбранных шаблонов, полностью заменяя старый.
        setPatterns(selectedPatterns);
    };
    
    const handleRemovePattern = (patternId: number) => {
        setPatterns(prevPatterns => prevPatterns.filter(p => p.id !== patternId));
    };

    const handlePatternChange = (patternId: number, field: 'name' | 'shablon', value: string) => {
        setPatterns(prevPatterns =>
            prevPatterns.map(p =>
                p.id === patternId ? { ...p, [field]: value } : p
            )
        );
    };

    const handleSave = () => {
        if (patterns.length === 0) {
            alert("Нельзя сохранить узел 'Проверка по шаблону' без добавления хотя бы одного шаблона.");
            return;
        }
        onSave(patterns);
    };

    if (!isOpen) {
        return null;
    }

    return (
        <>
            <div className="modal-overlay">
                <div className="modal-content pattern-check-modal">
                    <h2>Проверка по шаблону</h2>
                    <div className="table-container">
                        <table>
                            <thead>
                                <tr>
                                    <th>Название</th>
                                    <th>Шаблон</th>
                                    <th></th>
                                </tr>
                            </thead>
                            <tbody>
                                {patterns.map(pattern => (
                                    <tr key={pattern.id}>
                                        <td>
                                            <input
                                                type="text"
                                                value={pattern.name}
                                                onChange={(e) => handlePatternChange(pattern.id, 'name', e.target.value)}
                                                className="pattern-input"
                                            />
                                        </td>
                                        <td>
                                            <input
                                                type="text"
                                                value={pattern.shablon}
                                                onChange={(e) => handlePatternChange(pattern.id, 'shablon', e.target.value)}
                                                className="pattern-input"
                                            />
                                        </td>
                                        <td>
                                            <button 
                                                className="delete-pattern-btn"
                                                onClick={() => handleRemovePattern(pattern.id)}
                                            >
                                                &#x2715;
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                                {patterns.length === 0 && (
                                    <tr>
                                        <td colSpan={3}>Шаблоны не добавлены</td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                     <div className="add-template-button-container">
                         <button className="add-template-button" onClick={handleAddPatternClick}>+ Добавить шаблон</button>
                     </div>
                    <div className="modal-footer">
                        {onDelete && <button onClick={onDelete} className="delete-button">Удалить узел</button>}
                        <div className="footer-right-buttons">
                            <button onClick={onClose} className="cancel-button">Отмена</button>
                            <button onClick={handleSave} className="ok-button">Сохранить</button>
                        </div>
                    </div>
                </div>
            </div>

            <AddPatternModal
                isOpen={isAddPatternModalOpen}
                onClose={handleCloseAddPatternModal}
                onConfirm={handleConfirmAddPattern}
                existingPatterns={patterns}
            />
        </>
    );
};

export default PatternCheckModal; 