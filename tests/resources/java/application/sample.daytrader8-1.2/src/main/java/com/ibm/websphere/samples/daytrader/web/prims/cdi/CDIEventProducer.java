/**
 * (C) Copyright IBM Corporation 2019.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.prims.cdi;

import javax.annotation.Resource;
import javax.enterprise.concurrent.ManagedExecutorService;
import javax.enterprise.context.ApplicationScoped;
import javax.enterprise.event.Event;
import javax.enterprise.event.NotificationOptions;
import javax.inject.Inject;

@ApplicationScoped //?
public class CDIEventProducer {

  @Resource
  private ManagedExecutorService mes;

  @Inject
  @Hit
  Event<String> hitCountEvent;
  
  @Inject
  @HitAsync
  Event<String> hitCountEventAsync;
  
  public void produceSyncEvent() {
    hitCountEvent.fire("hitCount++");
  }

  public void produceAsyncEvent() {
    hitCountEventAsync.fireAsync("hitCount++", NotificationOptions.builder().setExecutor(mes).build());
  }
  
  

}
