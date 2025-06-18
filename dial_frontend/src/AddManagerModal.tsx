import React, { useState, useEffect, useMemo } from 'react';
import './AddManagerModal.css';
import { ManagerInfo } from './types';

interface AllNumbersData {
    user_id: number | null;
    full_name: string | null;
    phone_number: string;
    is_internal: boolean;
}

interface AddManagerModalProps {
    enterpriseId: string;
    onClose: () => void;
    onAdd: (selectedManagers: ManagerInfo[]) => void;
    addedPhones: Set<string>;
}

const AddManagerModal: React.FC<AddManagerModalProps> = ({ enterpriseId, onClose, onAdd, addedPhones }) => {
    const [allNumbers, setAllNumbers] = useState<AllNumbersData[]>([]);
    const [selectedPhones, setSelectedPhones] = useState<Set<string>>(new Set());
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchAllNumbers = async () => {
            try {
                const response = await fetch(`/dial/api/enterprises/${enterpriseId}/all_numbers_with_users`);
                if (!response.ok) {
                    throw new Error(`Failed to fetch numbers. Status: ${response.status}`);
                }
                const data: AllNumbersData[] = await response.json();
                setAllNumbers(data);
                
                // Сразу устанавливаем выбранные телефоны, если они есть
                setSelectedPhones(new Set(addedPhones));

            } catch (err) {
                setError('Не удалось загрузить список номеров.');
                console.error(err);
            } finally {
                setIsLoading(false);
            }
        };

        fetchAllNumbers();
    }, [enterpriseId, addedPhones]);
    
    // Мемоизируем отсортированный список для рендера
    const sortedNumbers = useMemo(() => {
        return [...allNumbers].sort((a, b) => {
            if (a.is_internal && !b.is_internal) return -1;
            if (!a.is_internal && b.is_internal) return 1;
            return a.phone_number.localeCompare(b.phone_number, undefined, { numeric: true });
        });
    }, [allNumbers]);

    const handleToggle = (phone: string) => {
        const newSelection = new Set(selectedPhones);
        if (newSelection.has(phone)) {
            newSelection.delete(phone);
        } else {
            newSelection.add(phone);
        }
        setSelectedPhones(newSelection);
    };

    const handleConfirm = () => {
        const selectionDetails = sortedNumbers
            .filter(num => selectedPhones.has(num.phone_number))
            .map(num => ({
                userId: num.user_id,
                name: num.full_name,
                phone: num.phone_number
            }));
        onAdd(selectionDetails);
        onClose();
    };

    const renderTable = () => {
        if (isLoading) return <p>Загрузка...</p>;
        if (error) return <p className="error">{error}</p>;
        if (sortedNumbers.length === 0) return <p>Номера не найдены.</p>;

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
                    {sortedNumbers.map((item) => (
                        <tr key={item.phone_number}>
                            <td>
                                <input
                                    type="checkbox"
                                    checked={selectedPhones.has(item.phone_number)}
                                    onChange={() => handleToggle(item.phone_number)}
                                />
                            </td>
                            <td>{item.phone_number}</td>
                            <td className={!item.full_name ? 'unassigned-user' : ''}>
                                {item.full_name || 'Не назначен'}
                            </td>
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
                    <h3>Добавить номер для обзвона</h3>
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