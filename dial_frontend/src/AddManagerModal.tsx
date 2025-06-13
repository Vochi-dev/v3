import React, { useState, useEffect } from 'react';
import './AddManagerModal.css';

// Определим типы для данных
interface ManagerData {
    id: number;
    full_name: string;
    internal_phones: string[];
    personal_phone: string | null;
}

interface FlattenedManager {
    userId: number;
    name: string;
    phone: string;
    isInternal: boolean;
}

interface AddManagerModalProps {
    enterpriseId: string;
    onClose: () => void;
    onAddManagers: (selectedManagers: FlattenedManager[]) => void;
    addedManagerIds: Set<number>;
}

const AddManagerModal: React.FC<AddManagerModalProps> = ({ enterpriseId, onClose, onAddManagers, addedManagerIds }) => {
    const [managers, setManagers] = useState<FlattenedManager[]>([]);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchManagers = async () => {
            try {
                const response = await fetch(`/dial/api/enterprises/${enterpriseId}/users`);
                if (!response.ok) {
                    const errorText = await response.text();
                    console.error("Server response:", errorText);
                    throw new Error(`Failed to fetch managers. Status: ${response.status}`);
                }
                const data: ManagerData[] = await response.json();
                
                // 1. Находим минимальный внутренний номер для каждого менеджера
                const managersWithMinPhone = data.map(user => {
                    const internalNumbers = user.internal_phones.map(p => parseInt(p, 10)).filter(p => !isNaN(p));
                    return {
                        ...user,
                        minInternal: internalNumbers.length > 0 ? Math.min(...internalNumbers) : Infinity,
                    };
                });

                // 2. Сортируем менеджеров по их минимальному номеру
                managersWithMinPhone.sort((a, b) => a.minInternal - b.minInternal);

                // 3. "Разворачиваем" отсортированных менеджеров в плоский список для отображения
                const flattened: FlattenedManager[] = [];
                managersWithMinPhone.forEach(user => {
                    // Сортируем внутренние номера менеджера
                    const sortedInternalPhones = user.internal_phones.sort((a,b) => parseInt(a, 10) - parseInt(b, 10));
                    
                    sortedInternalPhones.forEach(phone => {
                        flattened.push({ userId: user.id, name: user.full_name, phone, isInternal: true });
                    });
                    
                    // Добавляем личный номер в конце списка номеров менеджера
                    if (user.personal_phone) {
                        flattened.push({ userId: user.id, name: user.full_name, phone: user.personal_phone, isInternal: false });
                    }
                });
                
                setManagers(flattened);
            } catch (err) {
                setError('Не удалось загрузить список менеджеров.');
                console.error(err);
            } finally {
                setIsLoading(false);
            }
        };

        fetchManagers();
    }, [enterpriseId]);

    const handleToggle = (userId: number, phone: string) => {
        const selectionId = `${userId}-${phone}`;
        const newSelection = new Set(selected);
        if (newSelection.has(selectionId)) {
            newSelection.delete(selectionId);
        } else {
            newSelection.add(selectionId);
        }
        setSelected(newSelection);
    };

    const handleConfirm = () => {
        const selectedManagers = managers.filter(m => selected.has(`${m.userId}-${m.phone}`));
        onAddManagers(selectedManagers);
        onClose();
    };

    const renderTable = () => {
        if (isLoading) return <p>Загрузка...</p>;
        if (error) return <p className="error">{error}</p>;
        if (managers.length === 0) return <p>Менеджеры не найдены.</p>;

        return (
            <table className="manager-table">
                <thead>
                    <tr>
                        <th></th>
                        <th>Номер</th>
                        <th>Имя</th>
                    </tr>
                </thead>
                <tbody>
                    {managers.map((manager, index) => (
                        <tr key={`${manager.userId}-${manager.phone}-${index}`}>
                            <td>
                                <input
                                    type="checkbox"
                                    checked={selected.has(`${manager.userId}-${manager.phone}`)}
                                    onChange={() => handleToggle(manager.userId, manager.phone)}
                                    disabled={addedManagerIds.has(manager.userId)}
                                />
                            </td>
                            <td>{manager.phone}</td>
                            <td>{manager.name}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        );
    };

    return (
        <div className="add-manager-modal-overlay" onClick={onClose}>
            <div className="add-manager-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="add-manager-modal-header">
                    <h3>Добавить менеджера</h3>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="add-manager-modal-body">
                    {renderTable()}
                </div>
                <div className="add-manager-modal-footer">
                    <button className="cancel-button" onClick={onClose}>Отмена</button>
                    <button className="ok-button" onClick={handleConfirm}>OK</button>
                </div>
            </div>
        </div>
    );
};

export default AddManagerModal; 