import React, { useState, useEffect, useMemo } from 'react';
import { Node } from 'reactflow';
import './OutgoingCallModal.css';

interface AllNumbersData {
    user_id: number | null;
    full_name: string | null;
    phone_number: string;
    is_internal: boolean;
}

interface OutgoingCallModalProps {
  isOpen: boolean;
  onClose: () => void;
  enterpriseId: string;
  node: Node | null;
  onConfirm: (nodeId: string, data: any) => void;
  onDelete: (nodeId: string) => void;
}

const OutgoingCallModal: React.FC<OutgoingCallModalProps> = ({ 
  isOpen, 
  onClose,
  enterpriseId,
  node,
  onConfirm,
  onDelete
}) => {
    const [allInternalPhones, setAllInternalPhones] = useState<AllNumbersData[]>([]);
    const [selectedPhones, setSelectedPhones] = useState<Set<string>>(new Set());
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (node?.data?.phones) {
            setSelectedPhones(new Set(node.data.phones));
        } else {
            setSelectedPhones(new Set());
        }
    }, [node]);

    useEffect(() => {
        if (!isOpen) return;

        const fetchInternalPhones = async () => {
            setIsLoading(true);
            setError(null);
            try {
                const response = await fetch(`/dial/api/enterprises/${enterpriseId}/all_numbers_with_users`);
                if (!response.ok) throw new Error(`Ошибка сети: ${response.status}`);
                
                const data: AllNumbersData[] = await response.json();
                
                const internalOnly = data.filter(item => item.is_internal);
                
                setAllInternalPhones(internalOnly);
            } catch (err) {
                setError('Не удалось загрузить список номеров.');
                console.error(err);
            } finally {
                setIsLoading(false);
            }
        };

        fetchInternalPhones();
    }, [isOpen, enterpriseId]);
    
    const sortedPhones = useMemo(() => {
        const groupedByManager: { [key: string]: AllNumbersData[] } = {};
        const unassigned: AllNumbersData[] = [];

        allInternalPhones.forEach(phone => {
            if (phone.full_name) {
                if (!groupedByManager[phone.full_name]) {
                    groupedByManager[phone.full_name] = [];
                }
                groupedByManager[phone.full_name].push(phone);
            } else {
                unassigned.push(phone);
            }
        });

        Object.values(groupedByManager).forEach(group => {
            group.sort((a, b) => parseInt(a.phone_number, 10) - parseInt(b.phone_number, 10));
        });

        const sortedManagerGroups = Object.values(groupedByManager).sort((a, b) => {
            const minPhoneA = Math.min(...a.map(p => parseInt(p.phone_number, 10)));
            const minPhoneB = Math.min(...b.map(p => parseInt(p.phone_number, 10)));
            return minPhoneA - minPhoneB;
        });

        const sortedAssigned = sortedManagerGroups.flat();
        const sortedUnassigned = unassigned.sort((a, b) => parseInt(a.phone_number, 10) - parseInt(b.phone_number, 10));

        return [...sortedAssigned, ...sortedUnassigned];
    }, [allInternalPhones]);

    const handleToggle = (phone: string) => {
        const newSelection = new Set(selectedPhones);
        if (newSelection.has(phone)) {
            newSelection.delete(phone);
        } else {
            newSelection.add(phone);
        }
        setSelectedPhones(newSelection);
    };

    const handleConfirmClick = () => {
        if (node) {
            const selectedPhonesDetails = allInternalPhones.filter(p => selectedPhones.has(p.phone_number));
            onConfirm(node.id, { 
                phones: Array.from(selectedPhones),
                phones_details: selectedPhonesDetails 
            });
            onClose();
        }
    };
    
    const handleDeleteClick = () => {
        if(node) {
            onDelete(node.id);
            onClose();
        }
    }

    const renderContent = () => {
        if (isLoading) return <div className="loading-message">Загрузка...</div>;
        if (error) return <div className="error-message">{error}</div>;
        if (sortedPhones.length === 0) return <p>Внутренние номера (3-значные) не найдены.</p>;

        return (
            <div className="phones-table-container">
                <table className="phones-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>Номер</th>
                            <th>Имя</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sortedPhones.map((item) => (
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
            </div>
        );
    };

    if (!isOpen) {
        return null;
    }

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Настройка исходящего звонка</h3>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="modal-body">
                    {renderContent()}
                </div>
                <div className="modal-footer">
                     <button className="delete-button" onClick={handleDeleteClick}>Удалить узел</button>
                    <div className="footer-right-buttons">
                        <button className="cancel-button" onClick={onClose}>Отмена</button>
                        <button className="ok-button" onClick={handleConfirmClick}>ОК</button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default OutgoingCallModal; 