export const confirmViewTemplate = `
  <div v-show="tab===3" class="panel">
    <h2>确认入正式册<span class="role-tag" v-if="!canWrite">只读·门闸拒绝</span></h2>
    <div>
      <input v-model.number="confirmId" type="number" placeholder="占位 ID" style="width:7rem" :disabled="!canWrite" />
      <button @click="doConfirm" :disabled="!canWrite" :title="canWrite ? '' : '只读账号禁止写操作（后端 403）'">确认</button>
      <span class="readonly-note" v-if="!canWrite">只读账号确认将被后端门闸拒绝，正式册与审计零增</span>
    </div>
    <p class="err" v-if="actionErr">{{ actionErr }}</p>
    <h3>正式册（bookings）<span class="role-tag readonly" v-if="!canWrite">只读·零写入</span></h3>
    <table>
      <thead><tr><th>ID</th><th>档</th><th>当事人</th><th>席</th><th>快照代</th><th>态</th></tr></thead>
      <tbody>
        <tr v-for="b in bookings" :key="b.id">
          <td>{{ b.id }}</td><td>{{ b.slot_code }}</td><td>{{ b.party_key }}</td>
          <td>{{ b.seats }}</td><td>{{ b.snapshot_gen }}</td><td>{{ b.status }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
