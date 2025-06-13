import React, { useState, useEffect } from 'react';
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
    onConfirm: (periods: SchedulePeriod[]) => void;
    initialData?: { periods?: (Omit<SchedulePeriod, 'days'> & { days: string[] })[] };
    onDelete: () => void;
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

const calculateRestTime = (periods: SchedulePeriod[]): string => {
    const timeToMinutes = (time: string) => {
        const [hours, minutes] = time.split(':').map(Number);
        return hours * 60 + minutes;
    };
    const minutesToTime = (minutes: number) => {
        if (minutes >= 24 * 60) return '24:00';
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    };

    const dailyIntervals: { [key: string]: { start: number, end: number }[] } = {};
    for (const day of DAYS_OF_WEEK_ORDER) {
        dailyIntervals[day] = [];
    }

    for (const period of periods) {
        for (const day of period.days) {
            dailyIntervals[day].push({ start: timeToMinutes(period.startTime), end: timeToMinutes(period.endTime) });
        }
    }

    const freeTimeByDay: { [key: string]: string } = {};

    for (const day of DAYS_OF_WEEK_ORDER) {
        const sortedIntervals = dailyIntervals[day].sort((a, b) => a.start - b.start);
        const freeIntervals = [];
        let lastEndTime = 0;

        for (const interval of sortedIntervals) {
            if (interval.start > lastEndTime) {
                freeIntervals.push(`${minutesToTime(lastEndTime)}-${minutesToTime(interval.start)}`);
            }
            lastEndTime = Math.max(lastEndTime, interval.end);
        }

        if (lastEndTime < 24 * 60) {
            freeIntervals.push(`${minutesToTime(lastEndTime)}-24:00`);
        }

        freeTimeByDay[day] = freeIntervals.join(', ');
    }

    const groupedDays: { schedule: string, days: string[] }[] = [];

    for (const day of DAYS_OF_WEEK_ORDER) {
        const schedule = freeTimeByDay[day];
        const lastGroup = groupedDays[groupedDays.length - 1];
        if (lastGroup && lastGroup.schedule === schedule) {
            lastGroup.days.push(day);
        } else {
            groupedDays.push({ schedule, days: [day] });
        }
    }

    return groupedDays
        .filter(g => g.schedule)
        .map(g => `${formatDays(new Set(g.days))} ${g.schedule}`)
        .join('; ');
};

const WorkScheduleModal: React.FC<WorkScheduleModalProps> = ({ onClose, onConfirm, initialData, onDelete }) => {
    const [periods, setPeriods] = useState<SchedulePeriod[]>([]);

    useEffect(() => {
        if (initialData?.periods && initialData.periods.length > 0) {
            const initialPeriods = initialData.periods.map(p => ({
                ...p,
                days: new Set(p.days)
            }));
            setPeriods(initialPeriods);
        } else {
            // Устанавливаем значение по умолчанию, если нет initialData
            setPeriods([
                {
                    id: 1,
                    name: 'Рабочее время',
                    days: new Set(['пн', 'вт', 'ср', 'чт', 'пт']),
                    startTime: '09:00',
                    endTime: '18:00',
                }
            ]);
        }
    }, [initialData]);

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

    const handleConfirm = () => {
        // Сериализация Set в массив для передачи
        const periodsToSave = periods.map(p => ({
            ...p,
            days: Array.from(p.days)
        }));
        onConfirm(periodsToSave as any); 
    };

    const restTimeDescription = calculateRestTime(periods);

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
                                <tr className="rest-time-row">
                                    <td>Остальное время</td>
                                    <td>{restTimeDescription}</td>
                                    {periods.length > 1 && <td></td>}
                                </tr>
                            </tbody>
                        </table>
                        <button className="add-period-button" onClick={handleOpenAddModal}>
                            Добавить период времени
                        </button>
                    </div>
                    <div className="work-schedule-modal-footer">
                        <div className="footer-buttons-left">
                            <button className="delete-button" onClick={onDelete}>Удалить</button>
                        </div>
                        <div className="footer-buttons-right">
                            <button className="cancel-button" onClick={onClose}>Отмена</button>
                            <button className="ok-button" onClick={handleConfirm}>ОК</button>
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