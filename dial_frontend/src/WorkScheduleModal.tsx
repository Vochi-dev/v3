import React, { useState } from 'react';
import './WorkScheduleModal.css';
import AddPeriodModal from './AddPeriodModal';

export interface SchedulePeriod {
    id: number;
    name: string;
    days: Set<string>;
    startTime: string;
    endTime: string;
}

interface WorkScheduleModalProps {
    onClose: () => void;
}

const DAYS_OF_WEEK_ORDER = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];

// Функция для форматирования дней недели
const formatDays = (days: Set<string>): string => {
    const sortedDays = DAYS_OF_WEEK_ORDER.filter(day => days.has(day));
    if (sortedDays.length === 0) return '';

    const ranges: string[] = [];
    let startRange = sortedDays[0];

    for (let i = 1; i <= sortedDays.length; i++) {
        const dayIndex = DAYS_OF_WEEK_ORDER.indexOf(sortedDays[i]);
        const prevDayIndex = DAYS_OF_WEEK_ORDER.indexOf(sortedDays[i - 1]);

        if (i === sortedDays.length || dayIndex !== prevDayIndex + 1) {
            const endRange = sortedDays[i - 1];
            if (startRange === endRange) {
                ranges.push(startRange);
            } else {
                ranges.push(`${startRange}-${endRange}`);
            }
            if (i < sortedDays.length) {
                startRange = sortedDays[i];
            }
        }
    }
    return ranges.join(', ');
};

const WorkScheduleModal: React.FC<WorkScheduleModalProps> = ({ onClose }) => {
    const [periods, setPeriods] = useState<SchedulePeriod[]>([
        {
            id: 1,
            name: 'Рабочее время',
            days: new Set(['пн', 'вт', 'ср', 'чт', 'пт']),
            startTime: '09:00',
            endTime: '18:00',
        }
    ]);
    const [isAddPeriodModalOpen, setIsAddPeriodModalOpen] = useState(false);
    const [editingPeriod, setEditingPeriod] = useState<SchedulePeriod | null>(null);

    const handleOpenAddModal = () => {
        setEditingPeriod(null);
        setIsAddPeriodModalOpen(true);
    };

    const handleOpenEditModal = (period: SchedulePeriod) => {
        setEditingPeriod(period);
        setIsAddPeriodModalOpen(true);
    };

    const handleSavePeriod = (data: Omit<SchedulePeriod, 'id'> & { id?: number }) => {
        const timeToMinutes = (time: string) => {
            const [hours, minutes] = time.split(':').map(Number);
            return hours * 60 + minutes;
        };

        const newPeriodStart = timeToMinutes(data.startTime);
        const newPeriodEnd = timeToMinutes(data.endTime);

        if (newPeriodStart >= newPeriodEnd) {
            alert('Время начала периода должно быть раньше времени его окончания.');
            return;
        }

        const overlappingPeriod = periods.find(p => {
            if (data.id && p.id === data.id) {
                return false; // Не сравнивать с самим собой при редактировании
            }
            const commonDays = [...p.days].filter(day => data.days.has(day));
            if (commonDays.length === 0) {
                return false; // Нет общих дней - нет пересечений
            }

            const existingStart = timeToMinutes(p.startTime);
            const existingEnd = timeToMinutes(p.endTime);

            // Проверка на пересечение
            return newPeriodStart < existingEnd && existingStart < newPeriodEnd;
        });

        if (overlappingPeriod) {
            const commonDays = [...overlappingPeriod.days].filter(day => data.days.has(day));
            alert(
                `Ошибка: Период "${data.name}" пересекается с периодом "${overlappingPeriod.name}".\n` +
                `Пересечение на днях: ${formatDays(new Set(commonDays))}\n` +
                `Конфликтный интервал: ${overlappingPeriod.startTime} - ${overlappingPeriod.endTime}`
            );
            return;
        }

        if (data.id) {
            // Редактирование
            setPeriods(prev => prev.map(p => p.id === data.id ? { ...data, id: data.id, days: new Set(data.days) } : p));
        } else {
            // Добавление
            const newPeriod: SchedulePeriod = {
                ...data,
                id: Date.now(),
                days: new Set(data.days)
            };
            setPeriods(prev => [...prev, newPeriod]);
        }
        setIsAddPeriodModalOpen(false);
        setEditingPeriod(null);
    };

    const handleDeletePeriod = (idToDelete: number) => {
        setPeriods(prev => prev.filter(p => p.id !== idToDelete));
    };

    return (
        <>
            <div className="work-schedule-modal-overlay" onClick={onClose}>
                <div className="work-schedule-modal-content" onClick={(e) => e.stopPropagation()}>
                    <div className="work-schedule-modal-header">
                        <h3>График работы</h3>
                        <button onClick={onClose} className="close-button">&times;</button>
                    </div>
                    <div className="work-schedule-modal-body">
                        <table className="work-schedule-table">
                            <thead>
                                <tr>
                                    <th>Период времени</th>
                                    <th>Описание</th>
                                    {periods.length > 1 && <th></th>}
                                </tr>
                            </thead>
                            <tbody>
                                {periods.map((period) => (
                                    <tr key={period.id}>
                                        <td>
                                            <button className="link-button" onClick={() => handleOpenEditModal(period)}>
                                                {period.name}
                                            </button>
                                        </td>
                                        <td>{`${formatDays(period.days)} ${period.startTime}-${period.endTime}`}</td>
                                        {periods.length > 1 && (
                                            <td>
                                                <button className="delete-period-button" onClick={() => handleDeletePeriod(period.id)}>
                                                    &times;
                                                </button>
                                            </td>
                                        )}
                                    </tr>
                                ))}
                                {periods.length === 0 && (
                                    <tr>
                                        <td colSpan={2} style={{ textAlign: 'center', color: '#888' }}>
                                            Нет добавленных периодов
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                        <button className="add-period-button" onClick={handleOpenAddModal}>
                            Добавить период времени
                        </button>
                    </div>
                    <div className="work-schedule-modal-footer">
                        <div className="footer-buttons-left">
                            <button className="delete-button">Удалить</button>
                        </div>
                        <div className="footer-buttons-right">
                            <button className="cancel-button" onClick={onClose}>Отмена</button>
                            <button className="ok-button">ОК</button>
                        </div>
                    </div>
                </div>
            </div>
            {isAddPeriodModalOpen && (
                <AddPeriodModal 
                    initialData={editingPeriod}
                    onClose={() => setIsAddPeriodModalOpen(false)}
                    onConfirm={handleSavePeriod}
                />
            )}
        </>
    );
};

export default WorkScheduleModal; 