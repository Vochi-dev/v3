import React, { useState, useEffect } from 'react';
import './AddPeriodModal.css';
import { SchedulePeriod } from './WorkScheduleModal';

const weekDays = [
    { key: 'пн', label: 'ПН' },
    { key: 'вт', label: 'ВТ' },
    { key: 'ср', label: 'СР' },
    { key: 'чт', label: 'ЧТ' },
    { key: 'пт', label: 'ПТ' },
    { key: 'сб', label: 'СБ' },
    { key: 'вс', label: 'ВС' }
];

const TimePicker = ({ value, onChange }: { value: string, onChange: (value: string) => void }) => {
    const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));
    const minutes = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, '0'));

    const [hour, minute] = value.split(':');

    const handleHourChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        onChange(`${e.target.value}:${minute}`);
    };

    const handleMinuteChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        onChange(`${hour}:${e.target.value}`);
    };

    return (
        <div className="time-picker">
            <select value={hour} onChange={handleHourChange}>
                {hours.map(h => <option key={h} value={h}>{h}</option>)}
            </select>
            <span>:</span>
            <select value={minute} onChange={handleMinuteChange}>
                {minutes.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
        </div>
    );
};

interface AddPeriodModalProps {
    onClose: () => void;
    onConfirm: (data: Omit<SchedulePeriod, 'id'> & { id?: number }) => void;
    initialData?: SchedulePeriod | null;
}

const AddPeriodModal: React.FC<AddPeriodModalProps> = ({ onClose, onConfirm, initialData }) => {
    const [name, setName] = useState('График работы 1');
    const [selectedDays, setSelectedDays] = useState<Set<string>>(new Set(['пн', 'вт', 'ср', 'чт', 'пт']));
    const [startTime, setStartTime] = useState('09:00');
    const [endTime, setEndTime] = useState('18:00');

    useEffect(() => {
        if (initialData) {
            setName(initialData.name);
            setSelectedDays(new Set(initialData.days));
            setStartTime(initialData.startTime);
            setEndTime(initialData.endTime);
        } else {
            // Reset to default for new period
            setName('График работы 1');
            setSelectedDays(new Set(['пн', 'вт', 'ср', 'чт', 'пт']));
            setStartTime('09:00');
            setEndTime('18:00');
        }
    }, [initialData]);

    const handleDayClick = (day: string) => {
        const newSelectedDays = new Set(selectedDays);
        if (newSelectedDays.has(day)) {
            newSelectedDays.delete(day);
        } else {
            newSelectedDays.add(day);
        }
        setSelectedDays(newSelectedDays);
    };

    const handleConfirmClick = () => {
        if (name.trim() === '') {
            alert('Название периода не может быть пустым');
            return;
        }
        
        const dataToReturn: Omit<SchedulePeriod, 'id'> & { id?: number } = {
            name,
            days: selectedDays,
            startTime,
            endTime
        };

        if (initialData?.id) {
            dataToReturn.id = initialData.id;
        }

        onConfirm(dataToReturn);
    };

    return (
        <div className="add-period-modal-overlay" onClick={onClose}>
            <div className="add-period-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="add-period-modal-header">
                    <h2>{initialData ? 'Редактировать период' : 'Добавить период времени'}</h2>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="add-period-modal-body">
                    <div className="form-group">
                        <label htmlFor="period-name">Название</label>
                        <input
                            type="text"
                            id="period-name"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                        />
                    </div>
                    <div className="form-group">
                        <label>Интервал времени</label>
                        <div className="time-interval-inputs">
                            <TimePicker value={startTime} onChange={setStartTime} />
                            <span className="time-separator">&ndash;</span>
                            <TimePicker value={endTime} onChange={setEndTime} />
                        </div>
                    </div>
                    <div className="form-group">
                        <label>Дни недели</label>
                        <div className="days-of-week">
                            {weekDays.map((day) => (
                                <button
                                    key={day.key}
                                    className={`day-button ${selectedDays.has(day.key) ? 'selected' : ''}`}
                                    onClick={() => handleDayClick(day.key)}
                                >
                                    {day.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
                <div className="add-period-modal-footer">
                    <button onClick={handleConfirmClick} className="ok-button">OK</button>
                    <button onClick={onClose} className="cancel-button">Отмена</button>
                </div>
            </div>
        </div>
    );
};

export default AddPeriodModal; 