export const auditViewTemplate = `
  <div v-show="tab===5" class="panel">
    <h2>审计与争用<span class="role-tag readonly" v-if="!canWrite">只读视图</span></h2>
    <p class="readonly-note" v-if="!canWrite">只读确认在鉴权门前被拒，不产生审计行；后到者的撞锁只进下方争用区</p>
    <h3>争用（contention_log：谁撞了档锁）</h3>
    <table>
      <thead><tr><th>锁</th><th>人</th><th>动作</th><th>详情</th></tr></thead>
      <tbody>
        <tr v-for="c in contention" :key="c.id">
          <td>{{ c.lock_key }}</td><td>{{ c.actor }}</td><td>{{ c.action }}</td><td>{{ c.detail }}</td>
        </tr>
        <tr v-if="!contention.length"><td colspan="4" class="readonly-note">暂无争用痕</td></tr>
      </tbody>
    </table>
    <h3>审计（audit_trail：仅记有写者身份的真实写）</h3>
    <table>
      <thead><tr><th>人</th><th>动作</th><th>目标</th><th>时</th></tr></thead>
      <tbody>
        <tr v-for="a in audits" :key="a.id">
          <td>{{ a.actor }}</td><td>{{ a.action }}</td><td>{{ a.target }}</td><td>{{ a.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
