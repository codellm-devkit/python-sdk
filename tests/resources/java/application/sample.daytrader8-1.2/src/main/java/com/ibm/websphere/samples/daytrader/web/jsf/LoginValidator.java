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
package com.ibm.websphere.samples.daytrader.web.jsf;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.faces.application.FacesMessage;
import javax.faces.component.UIComponent;
import javax.faces.context.FacesContext;
import javax.faces.validator.FacesValidator;
import javax.faces.validator.Validator;
import javax.faces.validator.ValidatorException;

import com.ibm.websphere.samples.daytrader.util.Log;

@SuppressWarnings("rawtypes")
@FacesValidator("loginValidator")
public class LoginValidator implements Validator{

  static String loginRegex = "uid:\\d+";
  static Pattern pattern = Pattern.compile(loginRegex);
  static Matcher matcher;

  // Simple JSF validator to make sure username starts with uid: and at least 1 number.
  public LoginValidator() {
  }

  @Override
  public void validate(FacesContext context, UIComponent component, Object value) throws ValidatorException {
    Log.trace("LoginValidator.validate","Validating submitted login name -- " + value.toString());

    matcher = pattern.matcher(value.toString());

    if (!matcher.matches()) {
      FacesMessage msg = new FacesMessage("Username validation failed. Please provide username in this format: uid:#");
      msg.setSeverity(FacesMessage.SEVERITY_ERROR);

      throw new ValidatorException(msg);
    }
  }
}