export const reconcileViewTemplate = `
  <div v-show="tab===4" class="panel">
    <h2>对账互证<span class="role-tag readonly" v-if="!canWrite">只读视图</span><span class="role-tag" v-else>可写视图</span></h2>
    <p :class="recon.all_ok ? 'ok' : 'err'">{{ recon.all_ok ? '全部同源' : '存在分裂' }}</p>
    <p class="readonly-note" v-if="!canWrite">只读确认被门闸拒绝、不写 booked 与正式册，故对账保持同源</p>
    <table>
      <thead><tr><th>档</th><th>盘面已订</th><th>正式册席</th><th>结果</th></tr></thead>
      <tbody>
        <tr v-for="i in (recon.items||[])" :key="i.slot_code">
          <td>{{ i.slot_code }}</td><td>{{ i.slot_booked }}</td>
          <td>{{ i.booking_seats }}</td>
          <td :class="i.ok?'ok':'err'">{{ i.ok ? '通过' : '分裂' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
