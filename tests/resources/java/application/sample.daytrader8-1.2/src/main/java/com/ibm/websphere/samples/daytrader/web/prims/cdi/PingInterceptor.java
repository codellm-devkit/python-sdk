/**
 * (C) Copyright IBM Corporation 2015.
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

import java.io.Serializable;

import javax.annotation.Priority;
import javax.interceptor.AroundInvoke;
import javax.interceptor.Interceptor;
import javax.interceptor.InvocationContext;

/**
 *
 */
@PingInterceptorBinding
@Interceptor
@Priority(Interceptor.Priority.APPLICATION)
public class PingInterceptor implements Serializable {

  /**  */
  private static final long serialVersionUID = 1L;

  @AroundInvoke
  public Object methodInterceptor(InvocationContext ctx) throws Exception {

    //noop
    return ctx.proceed();
  }
}
