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
package com.ibm.websphere.samples.daytrader.impl.ejb3;


import javax.enterprise.context.Dependent;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.inject.Inject;

import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;


@Dependent
public class AsyncScheduledOrder implements Runnable {

  TradeServices tradeService;

  Integer orderID;
  boolean twoPhase;

  @Inject 
  public AsyncScheduledOrder(@Any Instance<TradeServices> services) {
    tradeService = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  public void setProperties(Integer orderID, boolean twoPhase) {
    this.orderID = orderID;
    this.twoPhase =  twoPhase;
  }     

  @Override
  public void run() {


    try {  
      tradeService.completeOrder(orderID, twoPhase);      

    } catch (Exception e) {

      e.printStackTrace();
    }
  }
}
