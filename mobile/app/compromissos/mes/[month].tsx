import React from 'react';
import {useLocalSearchParams} from 'expo-router';
import CommitmentProjection from '../../../components/CommitmentProjection';
import {api} from '../../../utils/sessionClient';

const money = (value: number) => new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: 2}).format(value || 0);

/** Addressable month. Opening it from the chart keeps this URL; the screen reads the same projection. */
export default function MonthRoute() {
  const params = useLocalSearchParams<{month?: string; periodo?: string}>();
  const month = String(params.month || '');
  const periodo = String(params.periodo || 'all');
  const months = periodo === 'all' || !/^\d+$/.test(periodo) ? null : Number(periodo);
  return <CommitmentProjection api={api.json} money={money} initialMonth={month} initialMonths={months} urlDriven />;
}
